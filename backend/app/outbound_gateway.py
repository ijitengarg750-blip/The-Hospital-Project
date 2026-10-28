from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from hashlib import sha256
import threading
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import database
from .config import get_settings
from .models import AuditLog, Machine, utcnow

router = APIRouter(prefix="/api")


@dataclass
class _ControlConnection:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any]]


@dataclass
class _DataConnection:
    websocket: WebSocket
    machine_id: int
    loop: asyncio.AbstractEventLoop
    claimed: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event)


class OutboundHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._controls: dict[int, _ControlConnection] = {}
        self._data: dict[str, _DataConnection] = {}
        self._used_tickets: dict[str, float] = {}

    def register(self, machine_id: int) -> _ControlConnection:
        connection = _ControlConnection(asyncio.get_running_loop(), asyncio.Queue())
        with self._lock:
            previous = self._controls.get(machine_id)
            self._controls[machine_id] = connection
        if previous:
            previous.loop.call_soon_threadsafe(
                previous.queue.put_nowait,
                {"type": "disconnect", "reason": "replaced"},
            )
        return connection

    def unregister(self, machine_id: int, connection: _ControlConnection) -> bool:
        with self._lock:
            if self._controls.get(machine_id) is connection:
                del self._controls[machine_id]
                return True
        return False

    def is_connected(self, machine_id: int) -> bool:
        with self._lock:
            return machine_id in self._controls

    def dispatch(self, machine_id: int, message: dict[str, Any]) -> bool:
        with self._lock:
            connection = self._controls.get(machine_id)
        if not connection:
            return False
        connection.loop.call_soon_threadsafe(connection.queue.put_nowait, message)
        return True

    def register_data(self, session_id: str, connection: _DataConnection) -> bool:
        with self._lock:
            if session_id in self._data:
                return False
            self._data[session_id] = connection
        return True

    def has_data(self, session_id: str, machine_id: int) -> bool:
        with self._lock:
            connection = self._data.get(session_id)
            return bool(connection and connection.machine_id == machine_id and not connection.claimed)

    def consume_ticket(self, ticket: str) -> bool:
        digest = sha256(ticket.encode()).hexdigest()
        now = time.monotonic()
        with self._lock:
            self._used_tickets = {key: used_at for key, used_at in self._used_tickets.items() if now - used_at < 600}
            if digest in self._used_tickets:
                return False
            self._used_tickets[digest] = now
        return True

    async def claim_data(self, session_id: str) -> _DataConnection | None:
        with self._lock:
            connection = self._data.get(session_id)
            if not connection or connection.claimed:
                return None
            connection.claimed = True
            return connection

    def release_data(self, session_id: str, connection: _DataConnection) -> None:
        with self._lock:
            if self._data.get(session_id) is connection:
                del self._data[session_id]
        connection.done.set()

    def close_data(self, session_id: str, reason: str) -> None:
        with self._lock:
            connection = self._data.pop(session_id, None)
        if not connection:
            return

        def close() -> None:
            connection.done.set()
            asyncio.create_task(connection.websocket.close(code=1012, reason=reason))

        connection.loop.call_soon_threadsafe(close)

    async def close_machine_data(self, machine_id: int) -> None:
        with self._lock:
            items = [(session_id, connection) for session_id, connection in self._data.items() if connection.machine_id == machine_id]
        for session_id, connection in items:
            self.release_data(session_id, connection)
            await connection.websocket.close(code=1012, reason="control connection lost")


outbound_hub = OutboundHub()


async def _send_control(websocket: WebSocket, connection: _ControlConnection) -> None:
    while True:
        message = await connection.queue.get()
        if message["type"] == "disconnect":
            await websocket.close(code=1012, reason=message["reason"])
            return
        await websocket.send_json(message)


def _authenticate(machine_code: str, agent_key: str):
    from .remote_sessions import authenticate_agent

    with database.SessionLocal() as db:
        agent = authenticate_agent(machine_code, agent_key, db)
        if not agent:
            return None
        return agent.machine_id


def _record_heartbeat(machine_id: int, state: str, components: dict | None = None) -> bool:
    from .remote_sessions import MachineAgent, record_agent_components

    if state not in {"unlocked", "locked", "signed_out", "booting", "busy", "unavailable"}:
        return False
    with database.SessionLocal() as db:
        agent = db.get(MachineAgent, machine_id)
        if not agent:
            return False
        agent.state = state
        agent.last_seen_at = utcnow()
        if isinstance(components, dict):
            record_agent_components(db, machine_id, components)
        db.commit()
    return True


def _update_session(machine_id: int, session_id: str, ready: bool, detail: str | None = None) -> bool:
    from .remote_sessions import RemoteSession

    if ready and not outbound_hub.has_data(session_id, machine_id):
        return False
    with database.SessionLocal() as db:
        session = db.get(RemoteSession, session_id)
        if not session or session.machine_id != machine_id or not session.active_slot or session.status != "requested":
            return False
        if ready:
            session.status = "ready"
            action = "remote.session.ready"
        else:
            session.status = "failed"
            session.active_slot = None
            session.ended_at = utcnow()
            session.end_reason = "agent_rejected"
            action = "remote.session.fail"
        resource = f"session:{session.id}"
        if detail:
            resource += f";detail:{detail[:120]}"
        db.add(AuditLog(username=f"agent:{machine_id}", action=action, resource=resource))
        db.commit()
    return True


@router.websocket("/agent/control")
async def agent_control(websocket: WebSocket):
    if get_settings().remote_access_mode != "outbound":
        await websocket.close(code=1008, reason="outbound mode disabled")
        return
    machine_id = _authenticate(
        websocket.headers.get("x-machine-code", ""),
        websocket.headers.get("x-agent-key", ""),
    )
    if not machine_id:
        await websocket.close(code=1008, reason="invalid machine credentials")
        return
    await websocket.accept()
    connection = outbound_hub.register(machine_id)
    with database.SessionLocal() as db:
        machine = db.get(Machine, machine_id)
        db.add(AuditLog(username=f"agent:{machine_id}", action="remote.control.connect", resource=machine.code if machine else None))
        db.commit()
    sender = asyncio.create_task(_send_control(websocket, connection))
    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await websocket.close(code=1008, reason="invalid control message")
                return
            if message.get("type") == "heartbeat" and _record_heartbeat(machine_id, message.get("state"), message.get("components")):
                outbound_hub.dispatch(machine_id, {"type": "heartbeat_ack"})
            elif message.get("type") in {"session_ready", "session_failed"} and _update_session(
                machine_id,
                str(message.get("session_id", "")),
                message["type"] == "session_ready",
                str(message.get("detail", "")) or None,
            ):
                continue
            else:
                await websocket.close(code=1008, reason="invalid control message")
                return
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        if outbound_hub.unregister(machine_id, connection):
            await outbound_hub.close_machine_data(machine_id)
            from .remote_sessions import fail_machine_sessions
            fail_machine_sessions(machine_id, "control_disconnected")
        await outbound_hub.close_machine_data(machine_id)
        from .remote_sessions import fail_machine_sessions
        fail_machine_sessions(machine_id, "control_disconnected")


@router.websocket("/agent/remote-sessions/{session_id}/data")
async def agent_data(websocket: WebSocket, session_id: str):
    if get_settings().remote_access_mode != "outbound":
        await websocket.close(code=1008, reason="outbound mode disabled")
        return
    machine_code = websocket.headers.get("x-machine-code", "")
    agent_key = websocket.headers.get("x-agent-key", "")
    machine_id = _authenticate(machine_code, agent_key)
    from .vnc_gateway import validate_tunnel_ticket
    from .remote_sessions import MachineAgent, RemoteSession
    with database.SessionLocal() as db:
        session = db.get(RemoteSession, session_id)
        identity = db.get(MachineAgent, machine_id) if machine_id else None
        valid = bool(
            session
            and identity
            and session.machine_id == machine_id
            and session.active_slot
            and session.status == "requested"
            and validate_tunnel_ticket(session_id, websocket.headers.get("x-session-ticket", ""), identity.key_hash)
        )
    if not valid:
        await websocket.close(code=1008, reason="invalid data authorization")
        return
    connection = _DataConnection(websocket=websocket, machine_id=machine_id, loop=asyncio.get_running_loop())
    ticket = websocket.headers.get("x-session-ticket", "")
    if not outbound_hub.consume_ticket(ticket) or not outbound_hub.register_data(session_id, connection):
        await websocket.close(code=1008, reason="data stream already exists")
        return
    try:
        await websocket.accept()
        await connection.done.wait()
    finally:
        outbound_hub.release_data(session_id, connection)
