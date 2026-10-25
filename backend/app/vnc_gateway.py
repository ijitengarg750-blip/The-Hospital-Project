from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import socket
import ssl
import struct
import time
from typing import Any

from Crypto.Cipher import DES
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .config import get_settings
from . import database
from .models import AuditLog, Machine
from .remote_sessions import RemoteSession, consume_viewer_ticket

router = APIRouter(prefix="/api")
_VERSION = re.compile(rb"RFB 003\.(\d{3})\n")
_TUNNEL_READY = b"HOSPITAL-RFB/1\n"


class GatewayConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MachineVncTarget:
    host: str
    port: int
    password: str = field(repr=False)


# Legacy direct-RFB helpers are retained for isolated compatibility tests only.
# The production WebSocket route always uses open_secure_transport below.
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


def open_secure_transport(machine: Machine, network: Any, settings) -> ssl.SSLSocket:
    if (
        not network
        or not network.agent_certificate_pem
        or not settings.vnc_gateway_client_cert_file
        or not settings.vnc_gateway_client_key_file
    ):
        raise GatewayConfigurationError("Mutual TLS transport is not configured")
    cert_file = Path(settings.vnc_gateway_client_cert_file)
    key_file = Path(settings.vnc_gateway_client_key_file)
    if not cert_file.is_absolute() or not key_file.is_absolute() or not cert_file.is_file() or not key_file.is_file():
        raise GatewayConfigurationError("Gateway client identity is not securely configured")
    raw = None
    connection = None
    try:
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cadata=network.agent_certificate_pem)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=cert_file, keyfile=key_file)
        raw = socket.create_connection((network.host, network.port), timeout=5)
        connection = context.wrap_socket(raw, server_hostname=network.tls_server_name)
        raw = None  # Ownership moved to the TLS socket.
        connection.settimeout(5)
        if _read_exact(connection, len(_TUNNEL_READY)) != _TUNNEL_READY:
            raise GatewayConfigurationError("Agent tunnel did not authorize the VNC connection")
        connection.settimeout(None)
        return connection
    except (OSError, ssl.SSLError, ValueError, GatewayConfigurationError) as exc:
        if connection:
            connection.close()
        elif raw:
            raw.close()
        raise GatewayConfigurationError("Mandatory encrypted VNC transport failed") from exc


def probe_secure_transport(machine: Machine, network: Any, settings) -> tuple[str, int | None, str | None]:
    started = time.monotonic()
    try:
        connection = open_secure_transport(machine, network, settings)
    except GatewayConfigurationError:
        return "unreachable", None, "Encrypted agent tunnel could not be established"
    connection.close()
    return "reachable", round((time.monotonic() - started) * 1000), None


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
    if origin not in settings.origins or not ticket_data:
        reason = "origin" if origin not in settings.origins else "ticket"
        await websocket.close(code=1008, reason=reason)
        return
    protocol, ticket = ticket_data
    with database.SessionLocal() as db:
        session = consume_viewer_ticket(db, session_id, ticket)
        if not session:
            await websocket.close(code=1008)
            return
        machine_code = db.get(Machine, session.machine_id).code
        machine = db.get(Machine, session.machine_id)
        from .machine_admin import MachineNetwork
        network = db.get(MachineNetwork, session.machine_id)
        db.add(AuditLog(username=session.doctor_username, action="remote.gateway.connect", resource=f"session:{session.id};machine:{machine_code}"))
        db.commit()
    upstream = None
    try:
        upstream = await asyncio.to_thread(open_secure_transport, machine, network, settings)
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
        if upstream:
            upstream.close()
