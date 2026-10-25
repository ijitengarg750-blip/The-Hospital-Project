from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import sessionmaker

from app import database, ingestion, machine_admin, main, security, vnc_gateway
from app.config import Settings
from app.database import Base, build_engine
from app.models import Patient, Study


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


def test_machine_registration_returns_separate_operational_states(client):
    machine = register_machine(client)

    assert machine["network"] == {"host": "10.20.30.40", "port": 5910, "tls_server_name": "SIRIUS-2"}
    assert machine["agent_status"] == "offline"
    assert machine["vnc_status"] == "unknown"
    assert machine["windows_status"] == "unknown"
    assert machine["session_status"] == "inactive"
    assert "password" not in str(machine).casefold()
    assert "private_key" not in str(machine).casefold()


def test_one_time_enrollment_rotation_and_revocation(client):
    machine = register_machine(client)
    issued = client.post(f"/api/machines/{machine['id']}/enrollments")
    assert issued.status_code == 201
    token = issued.json()["enrollment_token"]

    enrolled = client.post("/api/agent/enroll", json={"machine_code": machine["code"], "enrollment_token": token})
    assert enrolled.status_code == 201
    assert enrolled.headers["cache-control"] == "no-store"
    first_key = enrolled.json()["agent_key"]
    assert client.post("/api/agent/enroll", json={"machine_code": machine["code"], "enrollment_token": token}).status_code == 401
    first_headers = {"X-Machine-Code": machine["code"], "X-Agent-Key": first_key}
    assert client.post("/api/agent/heartbeat", headers=first_headers, json={"state": "locked"}).status_code == 200

    rotated_token = client.post(f"/api/machines/{machine['id']}/enrollments").json()["enrollment_token"]
    rotated = client.post("/api/agent/enroll", json={"machine_code": machine["code"], "enrollment_token": rotated_token})
    assert rotated.status_code == 201
    assert client.post("/api/agent/heartbeat", headers=first_headers, json={"state": "locked"}).status_code == 401
    rotated_headers = {"X-Machine-Code": machine["code"], "X-Agent-Key": rotated.json()["agent_key"]}
    assert client.post("/api/agent/heartbeat", headers=rotated_headers, json={"state": "signed_out"}).status_code == 200

    assert client.delete(f"/api/machines/{machine['id']}").status_code == 204
    assert client.post("/api/agent/heartbeat", headers=rotated_headers, json={"state": "signed_out"}).status_code == 401


def test_doctor_permission_is_required_for_remote_sessions(client):
    machine = register_machine(client)
    token = client.post(f"/api/machines/{machine['id']}/enrollments").json()["enrollment_token"]
    credential = client.post("/api/agent/enroll", json={"machine_code": machine["code"], "enrollment_token": token}).json()
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
    token = client.post(f"/api/machines/{machine['id']}/enrollments").json()["enrollment_token"]

    response = client.post("/api/agent/enroll", json={
        "machine_code": "NO-NET",
        "enrollment_token": token,
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
