from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
import secrets
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .config import get_settings
from .database import Base, get_db
from .models import AuditLog, CollectionEvent, Machine, MachineCollectorCredential, utcnow
from .remote_sessions import AGENT_ONLINE_WINDOW, AgentComponentHealth, MachineAgent, RemoteSession
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


class MachineEnrollmentClaim(Base):
    __tablename__ = "machine_enrollment_claims"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("machine_enrollments.id"), index=True)
    public_key_pem: Mapped[str] = mapped_column(Text)
    verification_code: Mapped[str] = mapped_column(String(6))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    encrypted_payload: Mapped[str | None] = mapped_column(Text)
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


class EnrollmentClaimBeginIn(BaseModel):
    machine_code: str = Field(min_length=2, max_length=100)
    public_key_pem: str = Field(min_length=200, max_length=4096)


class EnrollmentClaimCompleteIn(BaseModel):
    agent_certificate_pem: str | None = Field(default=None, max_length=16_384)


class EnrollmentClaimApprovalIn(BaseModel):
    verification_code: str = Field(pattern=r"^[0-9]{6}$")


class CredentialRotationIn(BaseModel):
    public_key_pem: str = Field(min_length=200, max_length=4096)


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


def _component_states(db: Session, machine: Machine, agent: MachineAgent | None, network: MachineNetwork | None, session: RemoteSession | None, online: bool) -> dict:
    reported = {
        item.name: item.status
        for item in db.scalars(select(AgentComponentHealth).where(AgentComponentHealth.machine_id == machine.id))
    } if online else {}
    tightvnc = reported.get("tightvnc")
    if tightvnc == "healthy" or (tightvnc is None and network and network.vnc_status == "reachable"):
        tightvnc_state = {"status": "Ready", "explanation": "TightVNC accepted the approved local readiness check."}
    elif tightvnc == "degraded" or (network and network.vnc_status == "unreachable"):
        tightvnc_state = {"status": "Error", "explanation": "Check the approved TightVNC service, loopback binding, and authentication in diagnostics."}
    else:
        tightvnc_state = {"status": "Unavailable", "explanation": "TightVNC readiness has not been confirmed by the diagnostic PC."}

    credential = db.get(MachineCollectorCredential, machine.id)
    collection = db.scalar(select(CollectionEvent).where(CollectionEvent.machine_code == machine.code).order_by(CollectionEvent.id.desc()))
    if not credential:
        collector_state = {"status": "Error", "explanation": "Data Collector credentials have not been configured."}
    elif not collection:
        collector_state = {"status": "Degraded", "explanation": "The Data Collector is configured but has not submitted a report yet."}
    elif collection.status in {"success", "duplicate"}:
        collector_state = {"status": "Working", "explanation": "The most recent Data Collector submission was accepted."}
    else:
        collector_state = {"status": "Error", "explanation": "The most recent Data Collector submission failed; review diagnostics and retry the queued report."}

    notification = reported.get("notification_ui")
    notification_state = (
        {"status": "Available", "explanation": "The Local Notification UI is reporting normally."} if notification == "healthy" else
        {"status": "Unavailable", "explanation": "Start or repair the Local Notification UI on the diagnostic PC."} if notification == "degraded" else
        {"status": "Unknown", "explanation": "The Local Notification UI has not reported its state."}
    )
    connector_state = (
        {"status": "Connected", "explanation": "The Hospital Remote Connector checked in recently."} if online else
        {"status": "Disconnected", "explanation": "The Hospital Remote Connector has not checked in recently."}
    )
    if session:
        remote_state = {"status": "Active", "explanation": "A doctor remote session is currently active."}
    elif online and tightvnc_state["status"] == "Ready":
        remote_state = {"status": "Ready", "explanation": "The connector and TightVNC checks required for a remote session have passed."}
    else:
        remote_state = {"status": "Not Ready", "explanation": "Connect the Remote Connector and confirm TightVNC readiness."}
    return {
        "remote_connector": connector_state,
        "tightvnc": tightvnc_state,
        "data_collector": collector_state,
        "local_notification_ui": notification_state,
        "remote_session": remote_state,
    }


def machine_out(db: Session, machine: Machine) -> dict:
    now = utcnow()
    agent = db.scalar(select(MachineAgent).where(MachineAgent.machine_id == machine.id))
    network = db.get(MachineNetwork, machine.id)
    session = db.scalar(select(RemoteSession).where(RemoteSession.machine_id == machine.id, RemoteSession.active_slot.is_(True)))
    online = bool(agent and agent.last_seen_at and now - _as_utc(agent.last_seen_at) <= AGENT_ONLINE_WINDOW)
    windows = agent.state if online and agent.state in {"locked", "unlocked", "signed_out"} else "unknown"
    claim = db.scalar(
        select(MachineEnrollmentClaim)
        .join(MachineEnrollment, MachineEnrollment.id == MachineEnrollmentClaim.enrollment_id)
        .where(
            MachineEnrollment.machine_id == machine.id,
            MachineEnrollmentClaim.used_at.is_(None),
            MachineEnrollmentClaim.expires_at > now,
        )
        .order_by(MachineEnrollmentClaim.created_at.desc())
    )
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
        "components": _component_states(db, machine, agent, network, session, online),
        "enrollment_claim": ({
            "verification_code": claim.verification_code,
            "status": "approved" if claim.approved_at else "pending",
            "expires_at": _as_utc(claim.expires_at),
        } if claim else None),
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
def create_enrollment(machine_id: int, response: Response, user: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db)):
    machine = db.get(Machine, machine_id)
    if not machine or not machine.active:
        raise HTTPException(404, "Machine not found")
    now = utcnow()
    for invitation in db.scalars(select(MachineEnrollment).where(MachineEnrollment.machine_id == machine.id, MachineEnrollment.used_at.is_(None))):
        invitation.used_at = now
    token = secrets.token_urlsafe(32)
    expires_at = now + ENROLLMENT_LIFETIME
    db.add(MachineEnrollment(machine_id=machine.id, token_hash=sha256(token.encode()).hexdigest(), expires_at=expires_at))
    db.add(AuditLog(username=str(user), action="machine.enrollment.issue", resource=machine.code))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"machine_code": machine.code, "expires_at": expires_at}


@router.post("/agent/enrollment-claims", status_code=201)
def begin_enrollment_claim(payload: EnrollmentClaimBeginIn, response: Response, db: Session = Depends(get_db)):
    from Crypto.PublicKey import RSA

    try:
        public_key = RSA.import_key(payload.public_key_pem)
        if public_key.has_private() or public_key.size_in_bits() < 2048:
            raise ValueError
    except (ValueError, IndexError, TypeError) as exc:
        raise HTTPException(422, "A valid 2048-bit or stronger public enrollment key is required") from exc
    now = utcnow()
    machine = db.scalar(select(Machine).where(Machine.code == payload.machine_code, Machine.active.is_(True)))
    if not machine:
        raise HTTPException(401, "No active enrollment invitation")
    enrollment = db.scalar(select(MachineEnrollment).where(
        MachineEnrollment.machine_id == machine.id,
        MachineEnrollment.used_at.is_(None),
        MachineEnrollment.expires_at > now,
    ).order_by(MachineEnrollment.created_at.desc()))
    if not enrollment:
        raise HTTPException(401, "No active enrollment invitation")
    attempts = list(db.scalars(select(MachineEnrollmentClaim.id).where(MachineEnrollmentClaim.enrollment_id == enrollment.id)))
    if len(attempts) >= 5:
        raise HTTPException(429, "Too many enrollment attempts; create a new invitation")
    claim = MachineEnrollmentClaim(
        id=str(uuid4()), enrollment_id=enrollment.id, public_key_pem=payload.public_key_pem,
        verification_code=f"{secrets.randbelow(1_000_000):06d}", expires_at=_as_utc(enrollment.expires_at),
    )
    db.add(claim)
    db.add(AuditLog(username=f"agent:{machine.code}", action="machine.enrollment.claim", resource=machine.code))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"claim_id": claim.id, "verification_code": claim.verification_code, "expires_at": _as_utc(claim.expires_at)}


@router.post("/machines/{machine_id}/enrollment-claims/approve", status_code=204)
def approve_enrollment_claim(machine_id: int, payload: EnrollmentClaimApprovalIn, user: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db)):
    now = utcnow()
    claim = db.scalar(
        select(MachineEnrollmentClaim)
        .join(MachineEnrollment, MachineEnrollment.id == MachineEnrollmentClaim.enrollment_id)
        .where(
            MachineEnrollment.machine_id == machine_id,
            MachineEnrollmentClaim.verification_code == payload.verification_code,
            MachineEnrollmentClaim.used_at.is_(None),
            MachineEnrollmentClaim.expires_at > now,
        ).order_by(MachineEnrollmentClaim.created_at.desc()).with_for_update()
    )
    if not claim:
        raise HTTPException(404, "Pending enrollment request not found")
    claim.approved_at = now
    machine = db.get(Machine, machine_id)
    db.add(AuditLog(username=str(user), action="machine.enrollment.approve", resource=machine.code if machine else str(machine_id)))
    db.commit()
    return Response(status_code=204)


@router.post("/agent/enrollment-claims/{claim_id}/complete", status_code=201)
def complete_enrollment_claim(claim_id: str, payload: EnrollmentClaimCompleteIn, response: Response, db: Session = Depends(get_db)):
    import base64
    import json
    from Crypto.Cipher import AES, PKCS1_OAEP
    from Crypto.Hash import SHA256
    from Crypto.PublicKey import RSA
    from Crypto.Random import get_random_bytes

    now = utcnow()
    claim = db.scalar(select(MachineEnrollmentClaim).where(MachineEnrollmentClaim.id == claim_id).with_for_update())
    if claim and claim.used_at and claim.encrypted_payload:
        response.headers["Cache-Control"] = "no-store"
        return {"encrypted_enrollment": json.loads(claim.encrypted_payload)}
    enrollment = db.get(MachineEnrollment, claim.enrollment_id) if claim else None
    machine = db.get(Machine, enrollment.machine_id) if enrollment else None
    if not claim or not enrollment or not machine or not machine.active or claim.used_at or enrollment.used_at or _as_utc(claim.expires_at) <= now:
        raise HTTPException(401, "Invalid or expired enrollment claim")
    if not claim.approved_at:
        raise HTTPException(409, "Waiting for administrator approval")
    result = _prepare_agent_credential(machine, enrollment, payload.agent_certificate_pem, db)
    content_key = get_random_bytes(32)
    cipher = AES.new(content_key, AES.MODE_GCM)
    ciphertext, tag = cipher.encrypt_and_digest(json.dumps(result).encode())
    encrypted_key = PKCS1_OAEP.new(RSA.import_key(claim.public_key_pem), hashAlgo=SHA256).encrypt(content_key)
    encrypted_payload = {
        "key": base64.b64encode(encrypted_key).decode(),
        "nonce": base64.b64encode(cipher.nonce).decode(),
        "tag": base64.b64encode(tag).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }
    claim.used_at = now
    claim.encrypted_payload = json.dumps(encrypted_payload)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"encrypted_enrollment": encrypted_payload}


@router.post("/machines/{machine_id}/collector-credentials", status_code=201)
def rotate_collector_credential(
    machine_id: int,
    payload: CredentialRotationIn,
    response: Response,
    user: AuthenticatedUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    import base64
    from Crypto.Cipher import PKCS1_OAEP
    from Crypto.Hash import SHA256
    from Crypto.PublicKey import RSA

    try:
        public_key = RSA.import_key(payload.public_key_pem)
        if public_key.has_private() or public_key.size_in_bits() < 2048:
            raise ValueError
    except (ValueError, IndexError, TypeError) as exc:
        raise HTTPException(422, "A valid 2048-bit or stronger public credential key is required") from exc
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
    encrypted_key = PKCS1_OAEP.new(public_key, hashAlgo=SHA256).encrypt(key.encode())
    db.add(AuditLog(username=str(user), action="machine.collector_credential.rotate", resource=machine.code))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"machine_code": machine.code, "encrypted_collector_key": base64.b64encode(encrypted_key).decode()}


def _prepare_agent_credential(machine: Machine, enrollment: MachineEnrollment, agent_certificate_pem: str | None, db: Session) -> dict:
    ca_certificate = ""
    if get_settings().remote_access_mode == "inbound_lab":
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
    if agent_certificate_pem:
        if get_settings().remote_access_mode != "inbound_lab":
            raise HTTPException(422, "Agent listener certificates are only accepted in inbound_lab mode")
        network = db.get(MachineNetwork, machine.id)
        if not network:
            raise HTTPException(409, "Configure the machine network before enrollment")
        if "PRIVATE KEY" in agent_certificate_pem:
            raise HTTPException(422, "Only the agent public certificate may be enrolled")
        try:
            import ssl
            ssl.PEM_cert_to_DER_cert(agent_certificate_pem)
        except ValueError as exc:
            raise HTTPException(422, "Agent certificate is not valid PEM") from exc
        network.agent_certificate_pem = agent_certificate_pem
    enrollment.used_at = utcnow()
    db.add(AuditLog(username=f"agent:{machine.code}", action="machine.enrollment.complete", resource=machine.code))
    return {"machine_code": machine.code, "agent_key": key, "gateway_ca_certificate": ca_certificate}


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
    result = _prepare_agent_credential(machine, enrollment, payload.agent_certificate_pem, db)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


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
