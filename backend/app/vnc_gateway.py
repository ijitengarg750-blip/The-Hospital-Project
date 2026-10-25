from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import socket
import struct

from Crypto.Cipher import DES
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .config import get_settings
from . import database
from .models import AuditLog, Machine
from .remote_sessions import RemoteSession, consume_viewer_ticket

router = APIRouter(prefix="/api")
_VERSION = re.compile(rb"RFB 003\.(\d{3})\n")


class GatewayConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MachineVncTarget:
    host: str
    port: int
    password: str = field(repr=False)


def _read_exact(connection, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = connection.recv(size - len(result))
        if not chunk:
            raise ConnectionError("VNC server closed the connection")
        result.extend(chunk)
    return bytes(result)


def _reverse_bits(value: int) -> int:
    value = ((value & 0x55) << 1) | ((value >> 1) & 0x55)
    value = ((value & 0x33) << 2) | ((value >> 2) & 0x33)
    return ((value << 4) | (value >> 4)) & 0xFF


def _vnc_response(challenge: bytes, password: str) -> bytes:
    try:
        encoded = password.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise GatewayConfigurationError("Classic VNC password must use Latin-1 characters") from exc
    if not 1 <= len(encoded) <= 8:
        raise GatewayConfigurationError("Classic VNC authentication requires a 1-to-8-byte password")
    key = encoded.ljust(8, b"\0")
    key = bytes(_reverse_bits(value) for value in key)
    return DES.new(key, DES.MODE_ECB).encrypt(challenge)


def authenticate_vnc(connection, password: str) -> None:
    banner = _read_exact(connection, 12)
    match = _VERSION.fullmatch(banner)
    if not match:
        raise GatewayConfigurationError("VNC server returned an invalid RFB version")
    server_minor = int(match.group(1))
    client_minor = 8 if server_minor >= 8 else 7 if server_minor >= 7 else 3
    connection.sendall(f"RFB 003.{client_minor:03d}\n".encode())
    if client_minor == 3:
        security_type = struct.unpack(">I", _read_exact(connection, 4))[0]
    else:
        count = _read_exact(connection, 1)[0]
        if count == 0:
            reason_size = struct.unpack(">I", _read_exact(connection, 4))[0]
            _read_exact(connection, min(reason_size, 1024))
            raise GatewayConfigurationError("VNC server rejected security negotiation")
        security_types = _read_exact(connection, count)
        if 2 not in security_types:
            raise GatewayConfigurationError("VNC server does not offer VNC authentication type 2")
        security_type = 2
        connection.sendall(b"\x02")
    if security_type != 2:
        raise GatewayConfigurationError("VNC server does not offer VNC authentication type 2")
    connection.sendall(_vnc_response(_read_exact(connection, 16), password))
    if struct.unpack(">I", _read_exact(connection, 4))[0] != 0:
        raise PermissionError("VNC authentication failed")


def load_machine_target(config_path: Path, machine_code: str) -> MachineVncTarget:
    try:
        item = json.loads(config_path.read_text(encoding="utf-8"))[machine_code]
        host = str(item["host"]).strip()
        port = int(item["port"])
        password_path = Path(item["password_file"])
        password = password_path.read_text(encoding="utf-8").rstrip("\r\n")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GatewayConfigurationError("VNC target is not securely configured") from exc
    if not host or not 1 <= port <= 65535 or not password or not password_path.is_absolute():
        raise GatewayConfigurationError("VNC target is not securely configured")
    return MachineVncTarget(host=host, port=port, password=password)


def _ticket_from_protocols(websocket: WebSocket) -> tuple[str, str] | None:
    for protocol in websocket.headers.get("sec-websocket-protocol", "").split(","):
        protocol = protocol.strip()
        if protocol.startswith("hospital-ticket.") and len(protocol) > len("hospital-ticket."):
            return protocol, protocol.removeprefix("hospital-ticket.")
    return None


async def _browser_handshake(websocket: WebSocket) -> None:
    await websocket.send_bytes(b"RFB 003.008\n")
    if not _VERSION.fullmatch(await websocket.receive_bytes()):
        raise GatewayConfigurationError("Viewer returned an invalid RFB version")
    await websocket.send_bytes(b"\x01\x01")
    if await websocket.receive_bytes() != b"\x01":
        raise GatewayConfigurationError("Viewer rejected the authenticated RFB stream")
    await websocket.send_bytes(b"\x00\x00\x00\x00")


async def _websocket_to_tcp(websocket: WebSocket, upstream: socket.socket) -> None:
    while True:
        upstream.sendall(await websocket.receive_bytes())


async def _tcp_to_websocket(upstream: socket.socket, websocket: WebSocket) -> None:
    while payload := await asyncio.to_thread(upstream.recv, 65536):
        await websocket.send_bytes(payload)


async def _watch_session(session_id: str) -> None:
    while True:
        await asyncio.sleep(1)
        with database.SessionLocal() as db:
            session = db.get(RemoteSession, session_id)
            if not session or not session.active_slot:
                return


@router.websocket("/remote-sessions/{session_id}/rfb")
async def remote_framebuffer(websocket: WebSocket, session_id: str):
    settings = get_settings()
    origin = websocket.headers.get("origin")
    ticket_data = _ticket_from_protocols(websocket)
    if origin not in settings.origins or not ticket_data or not settings.vnc_targets_file:
        reason = "origin" if origin not in settings.origins else "ticket" if not ticket_data else "configuration"
        await websocket.close(code=1008, reason=reason)
        return
    protocol, ticket = ticket_data
    with database.SessionLocal() as db:
        session = consume_viewer_ticket(db, session_id, ticket)
        if not session:
            await websocket.close(code=1008)
            return
        machine_code = db.get(Machine, session.machine_id).code
        try:
            target = load_machine_target(settings.vnc_targets_file, machine_code)
        except GatewayConfigurationError:
            await websocket.close(code=1011)
            return
        db.add(AuditLog(username=session.doctor_username, action="remote.gateway.connect", resource=f"session:{session.id};machine:{machine_code}"))
        db.commit()
    upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream.settimeout(5)
    try:
        await asyncio.to_thread(upstream.connect, (target.host, target.port))
        await asyncio.to_thread(authenticate_vnc, upstream, target.password)
        upstream.settimeout(None)
        await websocket.accept(subprotocol=protocol)
        await _browser_handshake(websocket)
        tasks = {
            asyncio.create_task(_websocket_to_tcp(websocket, upstream)),
            asyncio.create_task(_tcp_to_websocket(upstream, websocket)),
            asyncio.create_task(_watch_session(session_id)),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            if not task.cancelled():
                task.exception()
    except (OSError, ConnectionError, PermissionError, GatewayConfigurationError, WebSocketDisconnect):
        with database.SessionLocal() as db:
            session = db.get(RemoteSession, session_id)
            if session and session.active_slot and session.status == "connected":
                session.status = "failed"
                session.active_slot = None
                session.end_reason = "gateway_error"
                db.commit()
        if websocket.client_state.name == "CONNECTED":
            await websocket.close(code=1011)
    finally:
        upstream.close()
