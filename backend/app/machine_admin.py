from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .config import get_settings
from .database import Base, get_db
from .models import AuditLog, Machine, MachineCollectorCredential, utcnow
from .remote_sessions import AGENT_ONLINE_WINDOW, MachineAgent, RemoteSession
from .schemas import RemoteAccessUpdate
from .security import AuthenticatedUser, require_admin

router = APIRouter(prefix="/api")
ENROLLMENT_LIFETIME = timedelta(minutes=10)


class MachineNetwork(Base):
    __tablename__ = "machine_networks"
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), primary_key=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=5910)
    tls_server_name: Mapped[str] = mapped_column(String(255))
    agent_certificate_pem: Mapped[str | None] = mapped_column(Text)
    vnc_status: Mapped[str] = mapped_column(String(20), default="unknown")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(255))


class MachineEnrollment(Base):
    __tablename__ = "machine_enrollments"
    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MachinePermission(Base):
    __tablename__ = "machine_permissions"
    __table_args__ = (UniqueConstraint("machine_id", "doctor_username"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    doctor_username: Mapped[str] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NetworkInput(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5910, ge=1, le=65535)
    tls_server_name: str = Field(min_length=1, max_length=255)

    @field_validator("host", "tls_server_name")
    @classmethod
    def valid_network_name(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value) or "://" in value:
            raise ValueError("Use a hostname or IP address without a URL scheme")
        return value


class MachineCreateIn(RemoteAccessUpdate):
    code: str = Field(min_length=2, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=255)
    modality: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=255)
    no_vnc_url: str | None = None
    network: NetworkInput | None = None


class MachineUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    modality: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=255)
    network: NetworkInput | None = None


class EnrollmentIn(BaseModel):
    machine_code: str = Field(min_length=2, max_length=100)
    enrollment_token: str = Field(min_length=32, max_length=200)
    agent_certificate_pem: str | None = Field(default=None, max_length=16_384)


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    action: str
    resource: str | None
    created_at: datetime


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def require_machine_access(user: AuthenticatedUser, machine_id: int, db: Session) -> None:
    """Keep the machine permission check at the API boundary for every doctor route."""
    if user.role == "admin":
        return
    permission = db.scalar(select(MachinePermission.id).where(
        MachinePermission.machine_id == machine_id,
        MachinePermission.doctor_username == str(user),
    ))
    if permission is None:
        raise HTTPException(403, "You do not have access to this diagnostic machine")


def _network_out(network: MachineNetwork | None):
    if not network:
        return None
    return {"host": network.host, "port": network.port, "tls_server_name": network.tls_server_name}


def machine_out(db: Session, machine: Machine) -> dict:
    now = utcnow()
    agent = db.scalar(select(MachineAgent).where(MachineAgent.machine_id == machine.id))
    network = db.get(MachineNetwork, machine.id)
    session = db.scalar(select(RemoteSession).where(RemoteSession.machine_id == machine.id, RemoteSession.active_slot.is_(True)))
    online = bool(agent and agent.last_seen_at and now - _as_utc(agent.last_seen_at) <= AGENT_ONLINE_WINDOW)
    windows = agent.state if online and agent.state in {"locked", "unlocked", "signed_out"} else "unknown"
    return {
        "id": machine.id,
        "code": machine.code,
        "name": machine.name,
        "modality": machine.modality,
        "location": machine.location,
        "no_vnc_url": machine.no_vnc_url,
        "active": machine.active,
        "network": _network_out(network),
        "agent_status": "online" if online else "offline",
        "vnc_status": network.vnc_status if network else "unknown",
        "windows_status": windows,
        "session_status": "active" if session else "inactive",
        "last_seen_at": agent.last_seen_at if agent else None,
        "last_checked_at": network.last_checked_at if network else None,
        "last_error": network.last_error if network else None,
        "doctor_permissions": list(db.scalars(
            select(MachinePermission.doctor_username)
            .where(MachinePermission.machine_id == machine.id)
            .order_by(MachinePermission.doctor_username)
        )),
    }


@router.post("/machines", status_code=201)
def create_machine(payload: MachineCreateIn, user: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db)):
    if db.scalar(select(Machine).where(Machine.code == payload.code)):
        raise HTTPException(409, "Machine code already exists")
    machine = Machine(code=payload.code, name=payload.name.strip(), modality=payload.modality, location=payload.location, no_vnc_url=payload.no_vnc_url)
    db.add(machine)
    db.flush()
    if payload.network:
        db.add(MachineNetwork(machine_id=machine.id, **payload.network.model_dump()))
    db.add(AuditLog(username=str(user), action="machine.create", resource=payload.code))
    db.commit()
    return machine_out(db, machine)


@router.get("/machines")
def list_machines(_: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db)):
    return [machine_out(db, machine) for machine in db.scalars(select(Machine).order_by(Machine.name))]


@router.patch("/machines/{machine_id}")
def update_machine(machine_id: int, payload: MachineUpdateIn, user: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db)):
    machine = db.get(Machine, machine_id)
    if not machine:
        raise HTTPException(404, "Machine not found")
    for field, value in payload.model_dump(exclude_unset=True, exclude={"network"}).items():
        setattr(machine, field, value.strip() if isinstance(value, str) else value)
    if payload.network:
        network = db.get(MachineNetwork, machine_id)
        if network:
            for field, value in payload.network.model_dump().items():
                setattr(network, field, value)
            network.vnc_status = "unknown"
            network.last_error = None
        else:
            db.add(MachineNetwork(machine_id=machine_id, **payload.network.model_dump()))
    db.add(AuditLog(username=str(user), action="machine.update", resource=machine.code))
    db.commit()
    return machine_out(db, machine)


@router.post("/machines/{machine_id}/enrollments", status_code=201)
def create_enrollment(machine_id: int, user: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db)):
    machine = db.get(Machine, machine_id)
    if not machine or not machine.active:
        raise HTTPException(404, "Machine not found")
    token = secrets.token_urlsafe(32)
    expires_at = utcnow() + ENROLLMENT_LIFETIME
    db.add(MachineEnrollment(machine_id=machine.id, token_hash=sha256(token.encode()).hexdigest(), expires_at=expires_at))
    db.add(AuditLog(username=str(user), action="machine.enrollment.issue", resource=machine.code))
    db.commit()
    return {"machine_code": machine.code, "enrollment_token": token, "expires_at": expires_at}


@router.post("/machines/{machine_id}/collector-credentials", status_code=201)
def rotate_collector_credential(
    machine_id: int,
    response: Response,
    user: AuthenticatedUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    machine = db.scalar(select(Machine).where(Machine.id == machine_id).with_for_update())
    if not machine or not machine.active:
        raise HTTPException(404, "Machine not found")
    key = secrets.token_urlsafe(32)
    key_hash = sha256(key.encode()).hexdigest()
    credential = db.get(MachineCollectorCredential, machine.id)
    if credential:
        credential.key_hash = key_hash
        credential.updated_at = utcnow()
    else:
        db.add(MachineCollectorCredential(machine_id=machine.id, key_hash=key_hash))
    db.add(AuditLog(username=str(user), action="machine.collector_credential.rotate", resource=machine.code))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"machine_code": machine.code, "collector_key": key}


@router.post("/agent/enroll", status_code=201)
def enroll_agent(payload: EnrollmentIn, response: Response, db: Session = Depends(get_db)):
    now = utcnow()
    machine = db.scalar(select(Machine).where(Machine.code == payload.machine_code, Machine.active.is_(True)))
    enrollment = None
    if machine:
        enrollment = db.scalar(select(MachineEnrollment).where(
            MachineEnrollment.machine_id == machine.id,
            MachineEnrollment.token_hash == sha256(payload.enrollment_token.encode()).hexdigest(),
            MachineEnrollment.used_at.is_(None),
        ).with_for_update())
    if not enrollment or _as_utc(enrollment.expires_at) <= now:
        raise HTTPException(401, "Invalid or expired enrollment")
    ca_file = get_settings().vnc_gateway_ca_cert_file
    if not ca_file or not ca_file.is_absolute() or not ca_file.is_file():
        raise HTTPException(503, "Gateway identity is not configured")
    ca_certificate = ca_file.read_text(encoding="utf-8")
    if "PRIVATE KEY" in ca_certificate or ca_certificate.count("-----BEGIN CERTIFICATE-----") != 1 or ca_certificate.count("-----END CERTIFICATE-----") != 1:
        raise HTTPException(503, "Gateway public CA certificate is invalid")
    key = secrets.token_urlsafe(32)
    identity = db.scalar(select(MachineAgent).where(MachineAgent.machine_id == machine.id))
    if identity:
        identity.key_hash = sha256(key.encode()).hexdigest()
        identity.state = "unavailable"
        identity.last_seen_at = None
    else:
        db.add(MachineAgent(machine_id=machine.id, key_hash=sha256(key.encode()).hexdigest()))
    if payload.agent_certificate_pem:
        network = db.get(MachineNetwork, machine.id)
        if not network:
            raise HTTPException(409, "Configure the machine network before enrollment")
        if "PRIVATE KEY" in payload.agent_certificate_pem:
            raise HTTPException(422, "Only the agent public certificate may be enrolled")
        try:
            import ssl
            ssl.PEM_cert_to_DER_cert(payload.agent_certificate_pem)
        except ValueError as exc:
            raise HTTPException(422, "Agent certificate is not valid PEM") from exc
        network.agent_certificate_pem = payload.agent_certificate_pem
    enrollment.used_at = now
    db.add(AuditLog(username=f"agent:{machine.code}", action="machine.enrollment.complete", resource=machine.code))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"machine_code": machine.code, "agent_key": key, "gateway_ca_certificate": ca_certificate}


@router.put("/machines/{machine_id}/permissions/{doctor_username}", status_code=204)
def grant_permission(machine_id: int, doctor_username: str, user: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db)):
    doctor_username = _validated_doctor_username(doctor_username)
    machine = db.get(Machine, machine_id)
    if not machine:
        raise HTTPException(404, "Machine not found")
    existing = db.scalar(select(MachinePermission).where(MachinePermission.machine_id == machine_id, MachinePermission.doctor_username == doctor_username))
    if not existing:
        db.add(MachinePermission(machine_id=machine_id, doctor_username=doctor_username))
        db.add(AuditLog(username=str(user), action="machine.permission.grant", resource=f"machine:{machine.code};doctor:{doctor_username}"))
        db.commit()


@router.delete("/machines/{machine_id}/permissions/{doctor_username}", status_code=204)
def revoke_permission(machine_id: int, doctor_username: str, user: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db)):
    doctor_username = _validated_doctor_username(doctor_username)
    permission = db.scalar(select(MachinePermission).where(MachinePermission.machine_id == machine_id, MachinePermission.doctor_username == doctor_username))
    if permission:
        db.delete(permission)
        db.add(AuditLog(username=str(user), action="machine.permission.revoke", resource=f"machine:{machine_id};doctor:{doctor_username}"))
        db.commit()


def _validated_doctor_username(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,100}", value):
        raise HTTPException(422, "Doctor username is invalid")
    return value


def test_secure_transport(machine: Machine, network: MachineNetwork) -> tuple[Literal["reachable", "unreachable"], int | None, str | None]:
    from .vnc_gateway import probe_secure_transport
    return probe_secure_transport(machine, network, get_settings())


@router.post("/machines/{machine_id}/test-connection")
def test_connection(machine_id: int, user: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db)):
    machine = db.get(Machine, machine_id)
    network = db.get(MachineNetwork, machine_id)
    if not machine or not machine.active or not network:
        raise HTTPException(404, "Machine network configuration not found")
    status, latency_ms, error = test_secure_transport(machine, network)
    network.vnc_status = status
    network.last_checked_at = utcnow()
    network.last_error = error
    db.add(AuditLog(username=str(user), action="machine.connection.test", resource=f"machine:{machine.code};result:{status}"))
    db.commit()
    return {"vnc_status": status, "latency_ms": latency_ms, "error": error, "checked_at": network.last_checked_at}


@router.delete("/machines/{machine_id}", status_code=204)
def disable_machine(machine_id: int, user: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db)):
    machine = db.get(Machine, machine_id)
    if not machine:
        raise HTTPException(404, "Machine not found")
    machine.active = False
    identity = db.scalar(select(MachineAgent).where(MachineAgent.machine_id == machine_id))
    if identity:
        identity.key_hash = sha256(secrets.token_bytes(32)).hexdigest()
        identity.state = "unavailable"
        identity.last_seen_at = None
    for session in db.scalars(select(RemoteSession).where(RemoteSession.machine_id == machine_id, RemoteSession.active_slot.is_(True))):
        session.status = "failed"
        session.active_slot = None
        session.ended_at = utcnow()
        session.end_reason = "machine_revoked"
    db.add(AuditLog(username=str(user), action="machine.revoke", resource=machine.code))
    db.commit()
    return Response(status_code=204)


@router.get("/audit-logs", response_model=list[AuditOut])
def audit_logs(limit: int = Query(default=100, ge=1, le=500), _: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db)):
    return list(db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)))
