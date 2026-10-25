from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import secrets
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column, selectinload

from .database import Base, get_db
from .models import AuditLog, Machine, Study, utcnow
from .security import AuthenticatedUser, current_user, require_admin

router = APIRouter(prefix="/api")
SESSION_LIFETIME = timedelta(minutes=5)
AGENT_ONLINE_WINDOW = timedelta(seconds=60)
VIEWER_TICKET_LIFETIME = timedelta(seconds=30)
MachineState = Literal["unlocked", "locked", "signed_out", "booting", "busy", "unavailable"]


class MachineAgent(Base):
    __tablename__ = "machine_agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), default="unavailable")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RemoteSession(Base):
    __tablename__ = "remote_sessions"
    __table_args__ = (UniqueConstraint("machine_id", "active_slot", name="uq_remote_session_active_machine"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id"), index=True)
    doctor_username: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    machine_state: Mapped[str] = mapped_column(String(30))
    connection_grant_hash: Mapped[str] = mapped_column(String(64))
    grant_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_slot: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(50))


class RemoteMessage(Base):
    __tablename__ = "remote_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("remote_sessions.id"), index=True)
    sender: Mapped[str] = mapped_column(String(30))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentCredentialOut(BaseModel):
    machine_code: str
    agent_key: str


class AgentHeartbeat(BaseModel):
    state: MachineState


class AgentStatusOut(BaseModel):
    machine_code: str
    state: MachineState
    last_seen_at: datetime


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    machine_id: int
    study_id: int
    doctor_username: str
    status: Literal["requested", "ready", "connected", "ended", "expired", "failed"]
    machine_state: MachineState
    created_at: datetime
    expires_at: datetime
    connected_at: datetime | None
    ended_at: datetime | None
    end_reason: str | None


class SessionCreatedOut(BaseModel):
    session: SessionOut
    connection_grant: str


class ConnectRequest(BaseModel):
    connection_grant: str


class ViewerTicketOut(BaseModel):
    viewer_ticket: str
    websocket_path: str


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=500)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sender: Literal["doctor", "local_operator"]
    body: str
    created_at: datetime


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _expire(session: RemoteSession, now: datetime) -> bool:
    if session.active_slot and _as_utc(session.expires_at) <= now:
        session.status = "expired"
        session.active_slot = None
        session.ended_at = now
        session.end_reason = "expired"
        return True
    return False


def _agent_for_request(
    x_machine_code: str = Header(default=""),
    x_agent_key: str = Header(default=""),
    db: Session = Depends(get_db),
) -> MachineAgent:
    identity = db.scalar(
        select(MachineAgent)
        .join(Machine, Machine.id == MachineAgent.machine_id)
        .where(Machine.code == x_machine_code, Machine.active.is_(True))
    )
    supplied_hash = sha256(x_agent_key.encode()).hexdigest()
    if not identity or not hmac.compare_digest(identity.key_hash, supplied_hash):
        raise HTTPException(401, "Invalid machine credentials")
    return identity


def _doctor_session(session_id: str, user: str, db: Session) -> RemoteSession:
    session = db.get(RemoteSession, session_id)
    if not session or session.doctor_username != user:
        raise HTTPException(404, "Remote session not found")
    return session


def _agent_session(session_id: str, agent: MachineAgent, db: Session) -> RemoteSession:
    session = db.get(RemoteSession, session_id)
    if not session or session.machine_id != agent.machine_id:
        raise HTTPException(404, "Remote session not found")
    return session


def _active_session(session: RemoteSession) -> RemoteSession:
    if not session.active_slot or session.status in {"ended", "expired", "failed"}:
        raise HTTPException(409, "Remote session has ended")
    return session


def consume_viewer_ticket(db: Session, session_id: str, ticket: str) -> RemoteSession | None:
    """Atomically consume the short-lived ticket before any VNC connection is opened."""
    now = utcnow()
    session = db.scalar(select(RemoteSession).where(RemoteSession.id == session_id).with_for_update())
    if (
        not session
        or session.status != "ready"
        or not session.active_slot
        or not session.grant_used_at
        or now - _as_utc(session.grant_used_at) > VIEWER_TICKET_LIFETIME
        or not hmac.compare_digest(session.connection_grant_hash, sha256(ticket.encode()).hexdigest())
    ):
        return None
    session.connection_grant_hash = sha256(secrets.token_bytes(32)).hexdigest()
    session.status = "connected"
    session.connected_at = now
    return session


@router.post("/machines/{machine_id}/agent-credentials", response_model=AgentCredentialOut, status_code=201)
def provision_agent_credentials(
    machine_id: int,
    user: AuthenticatedUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    machine = db.get(Machine, machine_id)
    if not machine or not machine.active:
        raise HTTPException(404, "Machine not found")
    key = secrets.token_urlsafe(32)
    identity = db.scalar(select(MachineAgent).where(MachineAgent.machine_id == machine_id))
    if identity:
        identity.key_hash = sha256(key.encode()).hexdigest()
        identity.state = "unavailable"
        identity.last_seen_at = None
    else:
        identity = MachineAgent(machine_id=machine_id, key_hash=sha256(key.encode()).hexdigest())
        db.add(identity)
    db.add(AuditLog(username=user, action="machine.agent.credential.rotate", resource=machine.code))
    db.commit()
    return {"machine_code": machine.code, "agent_key": key}


@router.post("/agent/heartbeat", response_model=AgentStatusOut)
def agent_heartbeat(
    payload: AgentHeartbeat,
    agent: MachineAgent = Depends(_agent_for_request),
    db: Session = Depends(get_db),
):
    agent.state = payload.state
    agent.last_seen_at = utcnow()
    machine = db.get(Machine, agent.machine_id)
    db.commit()
    return {"machine_code": machine.code, "state": agent.state, "last_seen_at": agent.last_seen_at}


@router.post("/studies/{study_id}/remote-sessions", response_model=SessionCreatedOut, status_code=201)
def create_remote_session(
    study_id: int,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    now = utcnow()
    study = db.scalar(select(Study).where(Study.id == study_id).options(selectinload(Study.machine)))
    if not study or not study.machine.active:
        raise HTTPException(404, "Remote access is not configured for this machine")
    identity = db.scalar(select(MachineAgent).where(MachineAgent.machine_id == study.machine_id))
    if not identity or not identity.last_seen_at or now - _as_utc(identity.last_seen_at) > AGENT_ONLINE_WINDOW:
        raise HTTPException(409, "Machine agent is offline")
    if identity.state in {"busy", "booting", "unavailable"}:
        raise HTTPException(409, f"Machine is {identity.state}")

    active = db.scalar(
        select(RemoteSession).where(
            RemoteSession.machine_id == study.machine_id,
            RemoteSession.active_slot.is_(True),
        )
    )
    if active and not _expire(active, now):
        raise HTTPException(409, "Another remote session is active for this machine")

    grant = secrets.token_urlsafe(32)
    session = RemoteSession(
        id=str(uuid4()),
        machine_id=study.machine_id,
        study_id=study.id,
        doctor_username=user,
        status="requested",
        machine_state=identity.state,
        connection_grant_hash=sha256(grant.encode()).hexdigest(),
        expires_at=now + SESSION_LIFETIME,
    )
    db.add(session)
    db.add(AuditLog(username=user, action="remote.session.request", resource=f"session:{session.id};machine:{study.machine.code}"))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Another remote session is active for this machine") from exc
    db.refresh(session)
    return {"session": session, "connection_grant": grant}


@router.get("/remote-sessions/{session_id}", response_model=SessionOut)
def get_remote_session(
    session_id: str,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    session = _doctor_session(session_id, user, db)
    if _expire(session, utcnow()):
        db.commit()
    return session


@router.post("/remote-sessions/{session_id}/connect", response_model=SessionOut)
def connect_remote_session(
    session_id: str,
    payload: ConnectRequest,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    now = utcnow()
    session = _doctor_session(session_id, user, db)
    if _expire(session, now):
        db.add(AuditLog(username=user, action="remote.session.expire", resource=f"session:{session.id}"))
        db.commit()
        raise HTTPException(410, "Remote session expired")
    if session.status != "ready" or session.grant_used_at:
        raise HTTPException(409, "Remote session is not ready for a new connection")
    supplied_hash = sha256(payload.connection_grant.encode()).hexdigest()
    if not hmac.compare_digest(session.connection_grant_hash, supplied_hash):
        raise HTTPException(401, "Invalid connection grant")
    session.status = "connected"
    session.grant_used_at = now
    session.connected_at = now
    db.add(AuditLog(username=user, action="remote.session.connect", resource=f"session:{session.id};machine:{session.machine_id}"))
    db.commit()
    return session


@router.post("/remote-sessions/{session_id}/viewer-tickets", response_model=ViewerTicketOut, status_code=201)
def issue_viewer_ticket(
    session_id: str,
    payload: ConnectRequest,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    session = db.scalar(
        select(RemoteSession)
        .where(RemoteSession.id == session_id, RemoteSession.doctor_username == user)
        .with_for_update()
    )
    if not session:
        raise HTTPException(404, "Remote session not found")
    now = utcnow()
    if _expire(session, now):
        db.commit()
        raise HTTPException(410, "Remote session expired")
    if session.status != "ready" or session.grant_used_at:
        raise HTTPException(409, "Remote session is not ready for a viewer")
    if not hmac.compare_digest(session.connection_grant_hash, sha256(payload.connection_grant.encode()).hexdigest()):
        raise HTTPException(401, "Invalid connection grant")
    ticket = secrets.token_urlsafe(32)
    session.connection_grant_hash = sha256(ticket.encode()).hexdigest()
    session.grant_used_at = now
    db.add(AuditLog(username=user, action="remote.viewer.ticket.issue", resource=f"session:{session.id}"))
    db.commit()
    return {"viewer_ticket": ticket, "websocket_path": f"/api/remote-sessions/{session.id}/rfb"}


@router.delete("/remote-sessions/{session_id}", response_model=SessionOut)
def end_remote_session(
    session_id: str,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    session = _doctor_session(session_id, user, db)
    if session.active_slot:
        session.status = "ended"
        session.active_slot = None
        session.ended_at = utcnow()
        session.end_reason = "doctor"
        db.add(AuditLog(username=user, action="remote.session.end", resource=f"session:{session.id};reason:doctor"))
        db.commit()
    return session


@router.get("/agent/remote-sessions/next", response_model=SessionOut | None)
def next_agent_session(
    agent: MachineAgent = Depends(_agent_for_request),
    db: Session = Depends(get_db),
):
    session = db.scalar(
        select(RemoteSession)
        .where(RemoteSession.machine_id == agent.machine_id, RemoteSession.active_slot.is_(True))
        .order_by(RemoteSession.created_at)
    )
    if not session:
        return Response(status_code=204)
    if _expire(session, utcnow()):
        db.commit()
        return Response(status_code=204)
    return session


@router.post("/agent/remote-sessions/{session_id}/ready", response_model=SessionOut)
def agent_session_ready(
    session_id: str,
    agent: MachineAgent = Depends(_agent_for_request),
    db: Session = Depends(get_db),
):
    session = _agent_session(session_id, agent, db)
    if _expire(session, utcnow()):
        db.commit()
        raise HTTPException(410, "Remote session expired")
    if agent.state in {"busy", "booting", "unavailable"}:
        raise HTTPException(409, f"Machine is {agent.state}")
    if session.status != "requested":
        raise HTTPException(409, "Remote session cannot become ready")
    session.status = "ready"
    db.add(AuditLog(username=f"agent:{agent.machine_id}", action="remote.session.ready", resource=f"session:{session.id}"))
    db.commit()
    return session


@router.post("/agent/remote-sessions/{session_id}/end", response_model=SessionOut)
def agent_end_session(
    session_id: str,
    agent: MachineAgent = Depends(_agent_for_request),
    db: Session = Depends(get_db),
):
    session = _agent_session(session_id, agent, db)
    if session.active_slot:
        session.status = "ended"
        session.active_slot = None
        session.ended_at = utcnow()
        session.end_reason = "local_operator"
        db.add(AuditLog(username=f"agent:{agent.machine_id}", action="remote.session.end", resource=f"session:{session.id};reason:local_operator"))
        db.commit()
    return session


def _create_message(db: Session, session: RemoteSession, sender: str, audit_username: str, body: str) -> RemoteMessage:
    _active_session(session)
    message = RemoteMessage(session_id=session.id, sender=sender, body=body.strip())
    if not message.body:
        raise HTTPException(422, "Message cannot be blank")
    db.add(message)
    db.add(AuditLog(username=audit_username, action="remote.session.message", resource=f"session:{session.id};sender:{sender}"))
    db.commit()
    db.refresh(message)
    return message


@router.get("/remote-sessions/{session_id}/messages", response_model=list[MessageOut])
def doctor_messages(
    session_id: str,
    after: int = Query(default=0, ge=0),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    _doctor_session(session_id, user, db)
    return list(db.scalars(select(RemoteMessage).where(RemoteMessage.session_id == session_id, RemoteMessage.id > after).order_by(RemoteMessage.id).limit(100)))


@router.post("/remote-sessions/{session_id}/messages", response_model=MessageOut, status_code=201)
def doctor_message(
    session_id: str,
    payload: MessageIn,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    return _create_message(db, _doctor_session(session_id, user, db), "doctor", user, payload.body)


@router.get("/agent/remote-sessions/{session_id}/messages", response_model=list[MessageOut])
def agent_messages(
    session_id: str,
    after: int = Query(default=0, ge=0),
    agent: MachineAgent = Depends(_agent_for_request),
    db: Session = Depends(get_db),
):
    _agent_session(session_id, agent, db)
    return list(db.scalars(select(RemoteMessage).where(RemoteMessage.session_id == session_id, RemoteMessage.id > after).order_by(RemoteMessage.id).limit(100)))


@router.post("/agent/remote-sessions/{session_id}/messages", response_model=MessageOut, status_code=201)
def agent_message(
    session_id: str,
    payload: MessageIn,
    agent: MachineAgent = Depends(_agent_for_request),
    db: Session = Depends(get_db),
):
    return _create_message(db, _agent_session(session_id, agent, db), "local_operator", f"agent:{agent.machine_id}", payload.body)

