from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json

from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import sessionmaker

from app import database, ingestion, machine_admin, main, remote_sessions, security, vnc_gateway
from app.config import Settings
from app.database import Base, build_engine
from app.models import CollectionEvent, Patient, Study, utcnow


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
        session_cookie_secure=False,
        remote_access_mode="inbound_lab",
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
    monkeypatch.setattr(remote_sessions, "get_settings", lambda: settings)
    Base.metadata.create_all(engine)
    with TestClient(main.app) as test_client:
        yield test_client
    Base.metadata.drop_all(engine)


def login(client, role="admin"):
    credentials = {
        "admin": ("test-admin", "admin-password"),
        "doctor": ("test-doctor", "test-password"),
    }[role]
    response = client.post("/api/auth/login", json={"username": credentials[0], "password": credentials[1]})
    assert response.status_code == 200


def register_machine(client):
    login(client)
    response = client.post("/api/machines", json={
        "code": "SIRIUS-2",
        "name": "Sirius Room 2",
        "modality": "Corneal topography",
        "location": "Eye clinic, room 2",
        "network": {"host": "10.20.30.40", "port": 5910, "tls_server_name": "SIRIUS-2"},
    })
    assert response.status_code == 201
    return response.json()


def pair_machine(client, machine, approve=True):
    invitation = client.post(f"/api/machines/{machine['id']}/enrollments")
    assert invitation.status_code == 201
    assert invitation.headers["cache-control"] == "no-store"
    assert "token" not in invitation.text.casefold()
    private_key = RSA.generate(2048)
    claim = client.post("/api/agent/enrollment-claims", json={
        "machine_code": machine["code"],
        "public_key_pem": private_key.public_key().export_key().decode(),
    })
    assert claim.status_code == 201
    claim_data = claim.json()
    assert claim.headers["cache-control"] == "no-store"
    if not approve:
        return claim_data, private_key
    pending = next(item for item in client.get("/api/machines").json() if item["id"] == machine["id"])["enrollment_claim"]
    assert pending["verification_code"] == claim_data["verification_code"]
    assert "claim_id" not in pending
    approved = client.post(f"/api/machines/{machine['id']}/enrollment-claims/approve", json={"verification_code": claim_data["verification_code"]})
    assert approved.status_code == 204
    completed = client.post(f"/api/agent/enrollment-claims/{claim_data['claim_id']}/complete", json={})
    assert completed.status_code == 201
    encrypted = completed.json()["encrypted_enrollment"]
    content_key = PKCS1_OAEP.new(private_key, hashAlgo=SHA256).decrypt(base64.b64decode(encrypted["key"]))
    cipher = AES.new(content_key, AES.MODE_GCM, nonce=base64.b64decode(encrypted["nonce"]))
    decrypted = cipher.decrypt_and_verify(base64.b64decode(encrypted["ciphertext"]), base64.b64decode(encrypted["tag"]))
    claim_data["encrypted_enrollment"] = encrypted
    return json.loads(decrypted), claim_data


def rotate_collector(client, machine):
    private_key = RSA.generate(2048)
    response = client.post(f"/api/machines/{machine['id']}/collector-credentials", json={"public_key_pem": private_key.public_key().export_key().decode()})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert "collector_key" not in response.json()
    return PKCS1_OAEP.new(private_key, hashAlgo=SHA256).decrypt(base64.b64decode(response.json()["encrypted_collector_key"])).decode()


def test_machine_registration_returns_separate_operational_states(client):
    machine = register_machine(client)

    assert machine["network"] == {"host": "10.20.30.40", "port": 5910, "tls_server_name": "SIRIUS-2"}
    assert machine["agent_status"] == "offline"
    assert machine["vnc_status"] == "unknown"
    assert machine["windows_status"] == "unknown"
    assert machine["session_status"] == "inactive"
    assert machine["components"] == {
        "remote_connector": {
            "status": "Disconnected",
            "explanation": "The Hospital Remote Connector has not checked in recently.",
        },
        "tightvnc": {
            "status": "Unavailable",
            "explanation": "TightVNC readiness has not been confirmed by the diagnostic PC.",
        },
        "data_collector": {
            "status": "Error",
            "explanation": "Data Collector credentials have not been configured.",
        },
        "local_notification_ui": {
            "status": "Unknown",
            "explanation": "The Local Notification UI has not reported its state.",
        },
        "remote_session": {
            "status": "Not Ready",
            "explanation": "Connect the Remote Connector and confirm TightVNC readiness.",
        },
    }
    assert "password" not in str(machine).casefold()
    assert "private_key" not in str(machine).casefold()


def test_one_time_enrollment_rotation_and_revocation(client):
    machine = register_machine(client)
    enrolled, first_claim = pair_machine(client, machine)
    first_key = enrolled["agent_key"]
    repeated = client.post(f"/api/agent/enrollment-claims/{first_claim['claim_id']}/complete", json={})
    assert repeated.status_code == 201
    assert repeated.json()["encrypted_enrollment"] == first_claim["encrypted_enrollment"]
    first_headers = {"X-Machine-Code": machine["code"], "X-Agent-Key": first_key}
    assert client.post("/api/agent/heartbeat", headers=first_headers, json={"state": "locked"}).status_code == 200

    rotated, _ = pair_machine(client, machine)
    assert client.post("/api/agent/heartbeat", headers=first_headers, json={"state": "locked"}).status_code == 401
    rotated_headers = {"X-Machine-Code": machine["code"], "X-Agent-Key": rotated["agent_key"]}
    assert client.post("/api/agent/heartbeat", headers=rotated_headers, json={"state": "signed_out"}).status_code == 200

    assert client.delete(f"/api/machines/{machine['id']}").status_code == 204
    assert client.post("/api/agent/heartbeat", headers=rotated_headers, json={"state": "signed_out"}).status_code == 401


def test_expired_enrollment_invitation_is_rejected(client):
    machine = register_machine(client)
    client.post(f"/api/machines/{machine['id']}/enrollments")
    with database.SessionLocal() as db:
        enrollment = db.scalar(machine_admin.select(machine_admin.MachineEnrollment))
        enrollment.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    response = client.post("/api/agent/enrollment-claims", json={
        "machine_code": machine["code"],
        "public_key_pem": RSA.generate(2048).public_key().export_key().decode(),
    })

    assert response.status_code == 401
    assert response.json()["detail"] == "No active enrollment invitation"


def test_repeated_enrollment_attempts_require_a_new_invitation(client):
    machine = register_machine(client)
    assert client.post(f"/api/machines/{machine['id']}/enrollments").status_code == 201
    public_key = RSA.generate(2048).public_key().export_key().decode()

    claims = [
        client.post("/api/agent/enrollment-claims", json={
            "machine_code": machine["code"],
            "public_key_pem": public_key,
        })
        for _ in range(5)
    ]
    assert all(response.status_code == 201 for response in claims)

    blocked = client.post("/api/agent/enrollment-claims", json={
        "machine_code": machine["code"],
        "public_key_pem": public_key,
    })
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Too many enrollment attempts; create a new invitation"

    assert client.post(f"/api/machines/{machine['id']}/enrollments").status_code == 201
    replacement = client.post("/api/agent/enrollment-claims", json={
        "machine_code": machine["code"],
        "public_key_pem": public_key,
    })
    assert replacement.status_code == 201

    superseded_claim = claims[0].json()
    stale_approval = client.post(
        f"/api/machines/{machine['id']}/enrollment-claims/approve",
        json={"verification_code": superseded_claim["verification_code"]},
    )
    assert stale_approval.status_code == 404
    assert stale_approval.json()["detail"] == "Pending enrollment request not found"
    rejected = client.post(
        f"/api/agent/enrollment-claims/{superseded_claim['claim_id']}/complete",
        json={},
    )
    assert rejected.status_code == 401
    assert rejected.json()["detail"] == "Invalid or expired enrollment claim"


def test_enrollment_claim_waits_for_matching_admin_approval(client):
    machine = register_machine(client)
    claim, _ = pair_machine(client, machine, approve=False)
    assert client.post(f"/api/agent/enrollment-claims/{claim['claim_id']}/complete", json={}).status_code == 409
    login(client, "doctor")
    denied = client.post(f"/api/machines/{machine['id']}/enrollment-claims/approve", json={"verification_code": claim["verification_code"]})
    assert denied.status_code == 403


def test_component_health_is_reported_independently_and_without_agent_details(client):
    machine = register_machine(client)
    credential, _ = pair_machine(client, machine)
    headers = {"X-Machine-Code": machine["code"], "X-Agent-Key": credential["agent_key"]}

    heartbeat = client.post("/api/agent/heartbeat", headers=headers, json={
        "state": "unlocked",
        "components": {
            "tightvnc": {"status": "healthy", "error": "must not be stored or returned"},
            "notification_ui": {"status": "degraded", "error": "private local detail"},
        },
    })
    assert heartbeat.status_code == 200
    rotate_collector(client, machine)
    with database.SessionLocal() as db:
        db.add(CollectionEvent(machine_code=machine["code"], filename="queued.dcm", status="success", detail="private clinical detail"))
        db.commit()

    current = client.get("/api/machines").json()[0]
    assert current["components"]["remote_connector"]["status"] == "Connected"
    assert current["components"]["tightvnc"]["status"] == "Ready"
    assert current["components"]["data_collector"]["status"] == "Working"
    assert current["components"]["local_notification_ui"]["status"] == "Unavailable"
    assert current["components"]["remote_session"]["status"] == "Ready"
    assert "private local detail" not in str(current)
    assert "private clinical detail" not in str(current)


def test_component_health_requires_admin(client):
    register_machine(client)
    login(client, "doctor")
    assert client.get("/api/machines").status_code == 403


def test_collector_credentials_are_machine_scoped_and_rotate_independently(client):
    first = register_machine(client)
    second = client.post("/api/machines", json={"code": "OCT-2", "name": "OCT Room 2"}).json()
    agent, _ = pair_machine(client, first)
    agent_headers = {"X-Machine-Code": first["code"], "X-Agent-Key": agent["agent_key"]}

    first_key = rotate_collector(client, first)

    accepted = client.post(
        "/api/collector/upload",
        headers={"X-Machine-Code": first["code"], "X-Collector-Key": first_key},
        data={"machine_code": first["code"]},
    )
    wrong_machine = client.post(
        "/api/collector/upload",
        headers={"X-Machine-Code": second["code"], "X-Collector-Key": first_key},
        data={"machine_code": second["code"]},
    )
    assert accepted.status_code == 422
    assert "file" in accepted.text
    assert wrong_machine.status_code == 401

    rotated_key = rotate_collector(client, first)
    assert rotated_key != first_key
    assert client.post(
        "/api/collector/upload",
        headers={"X-Machine-Code": first["code"], "X-Collector-Key": first_key},
        data={"machine_code": first["code"]},
    ).status_code == 401
    assert client.post(
        "/api/collector/upload",
        headers={"X-Machine-Code": first["code"], "X-Collector-Key": rotated_key},
        data={"machine_code": first["code"]},
    ).status_code == 422
    assert client.post("/api/agent/heartbeat", headers=agent_headers, json={"state": "locked"}).status_code == 200


def test_doctor_permission_is_required_for_remote_sessions(client):
    machine = register_machine(client)
    credential, _ = pair_machine(client, machine)
    agent_headers = {"X-Machine-Code": machine["code"], "X-Agent-Key": credential["agent_key"]}
    client.post("/api/agent/heartbeat", headers=agent_headers, json={"state": "unlocked"})
    with database.SessionLocal() as db:
        study = Study(
            study_uid="permission-study",
            patient=Patient(practo_uid="10042"),
            machine_id=machine["id"],
            acquisition_datetime=datetime.now(timezone.utc),
        )
        db.add(study)
        db.commit()
        study_id = study.id

    login(client, "doctor")
    assert client.post(f"/api/studies/{study_id}/remote-sessions").status_code == 403
    login(client)
    assert client.put(f"/api/machines/{machine['id']}/permissions/test-doctor").status_code == 204
    login(client, "doctor")
    assert client.post(f"/api/studies/{study_id}/remote-sessions").status_code == 201


def test_connection_result_and_audit_log_are_real_but_secret_free(client, monkeypatch):
    machine = register_machine(client)
    monkeypatch.setattr(machine_admin, "test_secure_transport", lambda *_: ("reachable", 18, None))

    tested = client.post(f"/api/machines/{machine['id']}/test-connection")
    assert tested.status_code == 200
    assert tested.json()["vnc_status"] == "reachable"
    assert tested.json()["latency_ms"] == 18
    audit = client.get("/api/audit-logs?limit=20")
    assert audit.status_code == 200
    assert any(item["action"] == "machine.connection.test" for item in audit.json())
    assert "agent_key" not in audit.text.casefold()
    assert "password" not in audit.text.casefold()


def test_agent_certificate_enrollment_requires_network_configuration(client):
    login(client)
    machine = client.post("/api/machines", json={"code": "NO-NET", "name": "Not configured"}).json()
    claim, _ = pair_machine(client, machine, approve=False)
    assert client.post(f"/api/machines/{machine['id']}/enrollment-claims/approve", json={"verification_code": claim["verification_code"]}).status_code == 204
    response = client.post(f"/api/agent/enrollment-claims/{claim['claim_id']}/complete", json={
        "agent_certificate_pem": "-----BEGIN CERTIFICATE-----\ninvalid\n-----END CERTIFICATE-----",
    })

    assert response.status_code == 409
    assert "network" in response.json()["detail"].casefold()


def test_doctor_patient_history_is_filtered_by_machine_grant(client):
    first = register_machine(client)
    second = client.post("/api/machines", json={"code": "OCT-2", "name": "OCT Room 2"}).json()
    with database.SessionLocal() as db:
        patient = Patient(practo_uid="grant-filter")
        db.add_all([
            Study(study_uid="allowed-study", patient=patient, machine_id=first["id"], acquisition_datetime=datetime.now(timezone.utc)),
            Study(study_uid="denied-study", patient=patient, machine_id=second["id"], acquisition_datetime=datetime.now(timezone.utc)),
        ])
        db.commit()

    login(client, "doctor")
    assert client.get("/api/patients/grant-filter").status_code == 404
    login(client)
    assert client.put(f"/api/machines/{first['id']}/permissions/test-doctor").status_code == 204
    machines = client.get("/api/machines").json()
    assert next(item for item in machines if item["id"] == first["id"])["doctor_permissions"] == ["test-doctor"]
    login(client, "doctor")

    response = client.get("/api/patients/grant-filter")
    assert response.status_code == 200
    assert [study["study_uid"] for study in response.json()["studies"]] == ["allowed-study"]

    login(client)
    assert client.delete(f"/api/machines/{first['id']}/permissions/test-doctor").status_code == 204
    login(client, "doctor")
    assert client.get("/api/patients/grant-filter").status_code == 404
