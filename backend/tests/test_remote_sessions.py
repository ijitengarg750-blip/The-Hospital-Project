from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import database, ingestion, main, security
from app.config import Settings
from app.database import Base, build_engine
from app.models import Machine, Patient, Study


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'hospital-test.db'}",
        storage_root=tmp_path / "clinical-data",
        doctor_username="test-doctor",
        doctor_password="test-password",
        jwt_secret="test-secret-that-is-long-enough-for-a-disposable-suite",
        collector_api_key="test-collector-key",
    )
    engine = build_engine(settings.database_url)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(ingestion, "get_settings", lambda: settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    Base.metadata.create_all(engine)
    with TestClient(main.app) as test_client:
        yield test_client
    Base.metadata.drop_all(engine)


def doctor_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": "test-doctor", "password": "test-password"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_study() -> tuple[int, int]:
    with database.SessionLocal() as db:
        machine = Machine(code="SIRIUS-1", name="Sirius")
        study = Study(
            study_uid="remote-study",
            patient=Patient(practo_uid="10042"),
            machine=machine,
            acquisition_datetime=datetime.now(timezone.utc),
        )
        db.add(study)
        db.commit()
        return machine.id, study.id


def provision_agent(client: TestClient, machine_id: int, machine_code: str = "SIRIUS-1") -> tuple[str, dict[str, str]]:
    response = client.post(
        f"/api/machines/{machine_id}/agent-credentials",
        headers=doctor_headers(client),
    )
    assert response.status_code == 201
    key = response.json()["agent_key"]
    return key, {"X-Machine-Code": machine_code, "X-Agent-Key": key}


def test_agent_credential_is_returned_once_and_only_its_hash_is_stored(client):
    from app.remote_sessions import MachineAgent

    machine_id, _ = create_study()
    key, agent_headers = provision_agent(client, machine_id)

    with database.SessionLocal() as db:
        identity = db.scalar(select(MachineAgent).where(MachineAgent.machine_id == machine_id))
        assert identity is not None
        assert identity.key_hash == sha256(key.encode()).hexdigest()
        assert key not in repr(identity.__dict__)

    assert client.post("/api/agent/heartbeat", json={"state": "unlocked"}).status_code == 401
    assert client.post(
        "/api/agent/heartbeat",
        headers={**agent_headers, "X-Agent-Key": "wrong"},
        json={"state": "unlocked"},
    ).status_code == 401
    assert client.post("/api/agent/heartbeat", headers=agent_headers, json={"state": "unlocked"}).status_code == 200


def test_doctor_and_simulated_agent_complete_a_one_time_session(client):
    machine_id, study_id = create_study()
    _, agent_headers = provision_agent(client, machine_id)
    client.post("/api/agent/heartbeat", headers=agent_headers, json={"state": "locked"})
    doctor = doctor_headers(client)

    created = client.post(f"/api/studies/{study_id}/remote-sessions", headers=doctor)
    assert created.status_code == 201
    payload = created.json()
    session_id = payload["session"]["id"]
    grant = payload["connection_grant"]
    assert payload["session"]["status"] == "requested"
    assert payload["session"]["machine_state"] == "locked"

    pending = client.get("/api/agent/remote-sessions/next", headers=agent_headers)
    assert pending.status_code == 200
    assert pending.json()["id"] == session_id
    assert pending.json()["doctor_username"] == "test-doctor"

    ready = client.post(f"/api/agent/remote-sessions/{session_id}/ready", headers=agent_headers)
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"

    assert client.post(
        f"/api/remote-sessions/{session_id}/connect",
        headers=doctor,
        json={"connection_grant": "wrong"},
    ).status_code == 401

    connected = client.post(
        f"/api/remote-sessions/{session_id}/connect",
        headers=doctor,
        json={"connection_grant": grant},
    )
    assert connected.status_code == 200
    assert connected.json()["status"] == "connected"
    assert client.post(
        f"/api/remote-sessions/{session_id}/connect",
        headers=doctor,
        json={"connection_grant": grant},
    ).status_code == 409

    from app.models import AuditLog
    with database.SessionLocal() as db:
        audit_text = " ".join(item.resource or "" for item in db.scalars(select(AuditLog)))
        assert grant not in audit_text
        assert agent_headers["X-Agent-Key"] not in audit_text

    ended = client.post(f"/api/agent/remote-sessions/{session_id}/end", headers=agent_headers)
    assert ended.status_code == 200
    assert ended.json()["status"] == "ended"
    assert ended.json()["end_reason"] == "local_operator"
    assert client.post(
        f"/api/remote-sessions/{session_id}/connect",
        headers=doctor,
        json={"connection_grant": grant},
    ).status_code == 409


def test_machine_authorization_and_one_active_controller_are_enforced(client):
    machine_id, study_id = create_study()
    _, agent_headers = provision_agent(client, machine_id)
    client.post("/api/agent/heartbeat", headers=agent_headers, json={"state": "unlocked"})
    doctor = doctor_headers(client)
    first = client.post(f"/api/studies/{study_id}/remote-sessions", headers=doctor)
    assert first.status_code == 201
    assert client.post(f"/api/studies/{study_id}/remote-sessions", headers=doctor).status_code == 409

    with database.SessionLocal() as db:
        other = Machine(code="OCT-1", name="OCT")
        db.add(other)
        db.commit()
        other_id = other.id
    _, other_headers = provision_agent(client, other_id, "OCT-1")
    assert client.post(
        f"/api/agent/remote-sessions/{first.json()['session']['id']}/ready",
        headers=other_headers,
    ).status_code == 404


def test_busy_offline_expired_and_terminated_sessions_fail_closed(client):
    from app.remote_sessions import RemoteSession

    machine_id, study_id = create_study()
    _, agent_headers = provision_agent(client, machine_id)
    doctor = doctor_headers(client)
    assert client.post(f"/api/studies/{study_id}/remote-sessions", headers=doctor).status_code == 409

    client.post("/api/agent/heartbeat", headers=agent_headers, json={"state": "busy"})
    assert client.post(f"/api/studies/{study_id}/remote-sessions", headers=doctor).status_code == 409

    client.post("/api/agent/heartbeat", headers=agent_headers, json={"state": "unlocked"})
    created = client.post(f"/api/studies/{study_id}/remote-sessions", headers=doctor).json()
    session_id = created["session"]["id"]
    client.post("/api/agent/heartbeat", headers=agent_headers, json={"state": "busy"})
    assert client.post(f"/api/agent/remote-sessions/{session_id}/ready", headers=agent_headers).status_code == 409
    client.post("/api/agent/heartbeat", headers=agent_headers, json={"state": "unlocked"})
    assert client.post(f"/api/agent/remote-sessions/{session_id}/ready", headers=agent_headers).status_code == 200
    with database.SessionLocal() as db:
        session = db.get(RemoteSession, session_id)
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    expired = client.post(
        f"/api/remote-sessions/{session_id}/connect",
        headers=doctor,
        json={"connection_grant": created["connection_grant"]},
    )
    assert expired.status_code == 410
    assert client.get(f"/api/remote-sessions/{session_id}", headers=doctor).json()["status"] == "expired"

