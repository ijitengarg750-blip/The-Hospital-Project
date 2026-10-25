from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import socket
import struct
import threading

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import database, ingestion, machine_admin, main, security, vnc_gateway
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
        admin_username="test-admin",
        admin_password="admin-password",
        jwt_secret="test-secret-that-is-long-enough-for-a-disposable-suite",
        collector_api_key="test-collector-key",
        session_cookie_secure=False,
        vnc_gateway_ca_cert_file=(tmp_path / "gateway-ca.crt").resolve(),
    )
    settings.vnc_gateway_ca_cert_file.write_text("-----BEGIN CERTIFICATE-----\ntest-public-gateway-ca\n-----END CERTIFICATE-----\n", encoding="utf-8")
    engine = build_engine(settings.database_url)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(ingestion, "get_settings", lambda: settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(vnc_gateway, "get_settings", lambda: settings)
    monkeypatch.setattr(machine_admin, "get_settings", lambda: settings)
    Base.metadata.create_all(engine)
    with TestClient(main.app) as test_client:
        test_client.test_settings = settings
        test_client.test_tmp_path = tmp_path
        yield test_client
    Base.metadata.drop_all(engine)


def doctor_headers(client: TestClient) -> dict[str, str]:
    client.post("/api/auth/login", json={"username": "test-doctor", "password": "test-password"})
    return {}


def admin_headers(client: TestClient) -> dict[str, str]:
    client.post("/api/auth/login", json={"username": "test-admin", "password": "admin-password"})
    return {}


def create_study() -> tuple[int, int]:
    from app.machine_admin import MachinePermission

    with database.SessionLocal() as db:
        machine = Machine(code="SIRIUS-1", name="Sirius")
        study = Study(
            study_uid="remote-study",
            patient=Patient(practo_uid="10042"),
            machine=machine,
            acquisition_datetime=datetime.now(timezone.utc),
        )
        db.add(study)
        db.flush()
        db.add(MachinePermission(machine_id=machine.id, doctor_username="test-doctor"))
        db.commit()
        return machine.id, study.id


def provision_agent(client: TestClient, machine_id: int, machine_code: str = "SIRIUS-1") -> tuple[str, dict[str, str]]:
    issued = client.post(f"/api/machines/{machine_id}/enrollments", headers=admin_headers(client))
    response = client.post("/api/agent/enroll", json={
        "machine_code": machine_code,
        "enrollment_token": issued.json()["enrollment_token"],
    })
    assert response.status_code == 201
    key = response.json()["agent_key"]
    return key, {"X-Machine-Code": machine_code, "X-Agent-Key": key}


def test_agent_credential_is_returned_once_and_only_its_hash_is_stored(client):
    from app.remote_sessions import MachineAgent

    machine_id, _ = create_study()
    assert client.post(
        f"/api/machines/{machine_id}/enrollments",
        headers=doctor_headers(client),
    ).status_code == 403
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


def test_viewer_ticket_is_short_lived_single_use_and_contains_no_vnc_secret(client):
    from app.remote_sessions import consume_viewer_ticket

    machine_id, study_id = create_study()
    _, agent_headers = provision_agent(client, machine_id)
    client.post("/api/agent/heartbeat", headers=agent_headers, json={"state": "unlocked"})
    doctor = doctor_headers(client)
    created = client.post(f"/api/studies/{study_id}/remote-sessions", headers=doctor).json()
    session_id = created["session"]["id"]
    client.post(f"/api/agent/remote-sessions/{session_id}/ready", headers=agent_headers)

    issued = client.post(
        f"/api/remote-sessions/{session_id}/viewer-tickets",
        headers=doctor,
        json={"connection_grant": created["connection_grant"]},
    )
    assert issued.status_code == 201
    ticket = issued.json()["viewer_ticket"]
    assert issued.json()["websocket_path"] == f"/api/remote-sessions/{session_id}/rfb"
    assert "password" not in issued.text.casefold()

    with database.SessionLocal() as db:
        claimed = consume_viewer_ticket(db, session_id, ticket)
        assert claimed.machine_id == machine_id
        db.commit()
    with database.SessionLocal() as db:
        assert consume_viewer_ticket(db, session_id, ticket) is None


def test_doctor_and_local_operator_can_exchange_messages_until_session_ends(client):
    machine_id, study_id = create_study()
    _, agent_headers = provision_agent(client, machine_id)
    client.post("/api/agent/heartbeat", headers=agent_headers, json={"state": "locked"})
    doctor = doctor_headers(client)
    created = client.post(f"/api/studies/{study_id}/remote-sessions", headers=doctor).json()
    session_id = created["session"]["id"]
    client.post(f"/api/agent/remote-sessions/{session_id}/ready", headers=agent_headers)

    doctor_message = client.post(
        f"/api/remote-sessions/{session_id}/messages",
        headers=doctor,
        json={"body": "Please finish the current scan."},
    )
    assert doctor_message.status_code == 201
    assert doctor_message.json()["sender"] == "doctor"
    assert client.post(
        f"/api/agent/remote-sessions/{session_id}/messages",
        headers=agent_headers,
        json={"body": "Ready now."},
    ).status_code == 201

    messages = client.get(f"/api/remote-sessions/{session_id}/messages", headers=doctor).json()
    assert [(item["sender"], item["body"]) for item in messages] == [
        ("doctor", "Please finish the current scan."),
        ("local_operator", "Ready now."),
    ]
    assert client.get(
        f"/api/agent/remote-sessions/{session_id}/messages?after={messages[0]['id']}",
        headers=agent_headers,
    ).json()[0]["body"] == "Ready now."
    assert client.post(
        f"/api/remote-sessions/{session_id}/messages",
        headers=doctor,
        json={"body": "x" * 501},
    ).status_code == 422

    client.post(f"/api/agent/remote-sessions/{session_id}/end", headers=agent_headers)
    assert client.post(
        f"/api/remote-sessions/{session_id}/messages",
        headers=doctor,
        json={"body": "This must not be accepted."},
    ).status_code == 409


def test_websocket_gateway_uses_the_pre_authenticated_encrypted_agent_tunnel(client, monkeypatch):
    gateway_side, agent_side = socket.socketpair()
    observed = {}

    def fake_agent_tunnel():
        with agent_side:
            observed["shared"] = agent_side.recv(1)
            agent_side.sendall(
                struct.pack(">HH", 1, 1)
                + bytes.fromhex("2018000100ff00ff00ff100800000000")
                + struct.pack(">I", 4)
                + b"test"
            )

    server_thread = threading.Thread(target=fake_agent_tunnel, daemon=True)
    server_thread.start()
    monkeypatch.setattr(vnc_gateway, "open_secure_transport", lambda *_: gateway_side)

    machine_id, study_id = create_study()
    _, agent_headers = provision_agent(client, machine_id)
    client.post("/api/agent/heartbeat", headers=agent_headers, json={"state": "unlocked"})
    doctor = doctor_headers(client)
    created = client.post(f"/api/studies/{study_id}/remote-sessions", headers=doctor).json()
    session_id = created["session"]["id"]
    client.post(f"/api/agent/remote-sessions/{session_id}/ready", headers=agent_headers)
    access = client.post(
        f"/api/remote-sessions/{session_id}/viewer-tickets",
        headers=doctor,
        json={"connection_grant": created["connection_grant"]},
    ).json()
    protocol = f"hospital-ticket.{access['viewer_ticket']}"

    with client.websocket_connect(
        access["websocket_path"],
        headers={"origin": "http://localhost:5173"},
        subprotocols=[protocol],
    ) as websocket:
        assert websocket.accepted_subprotocol == protocol
        assert websocket.receive_bytes() == b"RFB 003.008\n"
        websocket.send_bytes(b"RFB 003.008\n")
        assert websocket.receive_bytes() == b"\x01\x01"
        websocket.send_bytes(b"\x01")
        assert websocket.receive_bytes() == b"\x00\x00\x00\x00"
        websocket.send_bytes(b"\x01")
        assert websocket.receive_bytes().endswith(b"test")

    server_thread.join(timeout=2)
    assert observed == {"shared": b"\x01"}

