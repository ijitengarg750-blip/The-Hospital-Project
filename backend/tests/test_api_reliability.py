from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import sessionmaker

from app import database, ingestion, main, security
from app.config import Settings
from app.database import Base, build_engine


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'hospital-test.db'}",
        storage_root=tmp_path / "clinical-data",
        doctor_username="test-doctor",
        doctor_password="test-password",
        jwt_secret="test-secret-that-is-long-enough-for-a-disposable-suite",
        collector_api_key="test-collector-key",
        max_upload_bytes=16,
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


def test_collector_rejects_absent_and_invalid_keys(client):
    absent = client.post("/api/collector/upload", data={"machine_code": "TEST-1"})
    invalid = client.post(
        "/api/collector/upload",
        headers={"X-Collector-Key": "invalid"},
        data={"machine_code": "TEST-1"},
    )

    assert absent.status_code == 401
    assert invalid.status_code == 401


def test_collector_accepts_a_valid_key_then_validates_request_shape(client):
    response = client.post(
        "/api/collector/upload",
        headers={"X-Collector-Key": "test-collector-key"},
        data={"machine_code": "TEST-1"},
    )

    assert response.status_code == 422
    assert "file" in response.text


def test_collector_applies_streaming_upload_limit_before_ingestion(client):
    response = client.post(
        "/api/collector/upload",
        headers={"X-Collector-Key": "test-collector-key"},
        data={"machine_code": "TEST-1"},
        files={"file": ("large.dcm", b"x" * 17, "application/dicom")},
    )

    assert response.status_code == 413


def test_api_data_endpoints_require_a_bearer_token(client):
    response = client.get("/api/patients/10042")

    assert response.status_code == 401


def test_login_rejects_wrong_credentials(client):
    response = client.post("/api/auth/login", json={"username": "test-doctor", "password": "wrong"})

    assert response.status_code == 401


def test_machine_registration_requires_login_and_rejects_duplicates(client):
    unauthenticated = client.post("/api/machines", json={"code": "TEST-1", "name": "Test"})
    login = client.post("/api/auth/login", json={"username": "test-doctor", "password": "test-password"})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/machines", headers=headers, json={"code": "TEST-1", "name": "Test"})
    duplicate = client.post("/api/machines", headers=headers, json={"code": "TEST-1", "name": "Test"})

    assert unauthenticated.status_code == 401
    assert login.status_code == 200
    assert created.status_code == 200
    assert duplicate.status_code == 409


def test_collector_rejects_unknown_machine_before_dicom_ingestion(client):
    response = client.post(
        "/api/collector/upload",
        headers={"X-Collector-Key": "test-collector-key"},
        data={"machine_code": "UNKNOWN"},
        files={"file": ("not-read.dcm", b"not a DICOM", "application/dicom")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown machine code: UNKNOWN"
