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


def auth_headers(client):
    response = client.post("/api/auth/login", json={"username": "test-doctor", "password": "test-password"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_remote_configuration_requires_login_and_persists(client):
    headers = auth_headers(client)
    machine = client.post("/api/machines", headers=headers, json={"code": "TEST-1", "name": "Sirius"}).json()
    path = f"/api/machines/{machine['id']}/remote-access"
    payload = {"no_vnc_url": "http://localhost:6080/vnc.html?autoconnect=true&view_only=true"}
    assert client.patch(path, json=payload).status_code == 401
    assert client.patch(path, headers=headers, json=payload).status_code == 200
    assert client.get("/api/machines", headers=headers).json()[0]["no_vnc_url"] == payload["no_vnc_url"]
    assert client.patch(path, headers=headers, json={"no_vnc_url": None}).json()["no_vnc_url"] is None


@pytest.mark.parametrize("url", ["javascript:alert(1)", "//example.com/vnc.html", "http://u:p@example.com", "http://example.com/?password=secret", "http://example.com/#password=secret"])
def test_remote_urls_reject_scripts_and_credentials(client, url):
    headers = auth_headers(client)
    assert client.post("/api/machines", headers=headers, json={"code": "BAD", "name": "Bad", "no_vnc_url": url}).status_code == 422


def test_report_selection_is_explicit_authenticated_and_reversible(client):
    from datetime import datetime
    from app.models import ClinicalFile, Machine, Patient, Study

    with database.SessionLocal() as db:
        study = Study(study_uid="report-selection", patient=Patient(practo_uid="10042"),
                      machine=Machine(code="REPORT-1", name="Sirius"), acquisition_datetime=datetime.now())
        image = ClinicalFile(study=study, kind="image", original_name="eye.jpg", relative_path="eye.jpg",
                             mime_type="image/jpeg", size_bytes=3, sha256="a" * 64, embedded_path="Scotopic/eye.jpg")
        db.add(image)
        db.commit()
        image_id = image.id
    path = f"/api/files/{image_id}/report-type"
    payload = {"report_type": "Corneal mapping summary"}
    assert client.patch(path, json=payload).status_code == 401
    headers = auth_headers(client)
    marked = client.patch(path, headers=headers, json=payload)
    assert marked.status_code == 200
    assert marked.json()["kind"] == "report_image"
    assert marked.json()["role"] == "Corneal mapping summary"
    assert client.patch(path, headers=headers, json={"report_type": "Unverified guess"}).status_code == 422
    restored = client.patch(path, headers=headers, json={"report_type": None}).json()
    assert restored["kind"] == "image"
    assert restored["role"] == "Scotopic images"


def test_report_gallery_returns_composed_images_only_and_caps_at_four(client):
    from datetime import datetime
    from hashlib import sha256
    from PIL import Image
    from app.models import ClinicalFile, Machine, Patient, Study

    with database.SessionLocal() as db:
        study = Study(study_uid="report-gallery", patient=Patient(practo_uid="10042"),
                      machine=Machine(code="REPORT-1", name="Sirius"), acquisition_datetime=datetime.now())
        db.add(study)
        db.flush()

        for index in range(5):
            relative_path = f"reports/composed-{index}.png"
            path = main.settings.storage_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (1024, 630), (index, 20, 30)).save(path)
            content = path.read_bytes()
            db.add(ClinicalFile(
                study=study, kind="image", original_name=f"composed-{index}.cso",
                relative_path=relative_path, mime_type="image/png", size_bytes=len(content),
                sha256=sha256(content).hexdigest(), embedded_path=f"composed-{index}.cso",
            ))

        raw_path = main.settings.storage_root / "reports/raw-eye.png"
        Image.new("RGB", (1024, 768), (10, 10, 10)).save(raw_path)
        raw_content = raw_path.read_bytes()
        db.add(ClinicalFile(
            study=study, kind="image", original_name="raw-eye.raw", relative_path="reports/raw-eye.png",
            mime_type="image/png", size_bytes=len(raw_content), sha256=sha256(raw_content).hexdigest(),
            embedded_path="raw-eye.raw",
        ))
        db.commit()
        study_id = study.id

    path = f"/api/studies/{study_id}/reports"
    assert client.get(path).status_code == 401
    response = client.get(path, headers=auth_headers(client))

    assert response.status_code == 200
    assert len(response.json()) == 4
    assert all(item["original_name"].startswith("composed-") for item in response.json())


def test_report_gallery_includes_pupillography_and_tear_film_cso_views(client):
    from datetime import datetime
    from hashlib import sha256
    from PIL import Image
    from app.models import ClinicalFile, Machine, Patient, Study

    with database.SessionLocal() as db:
        patient = Patient(practo_uid="10043")
        machine = Machine(code="REPORT-2", name="Sirius")
        studies = {
            title: Study(study_uid=f"report-gallery-{index}", patient=patient, machine=machine,
                         title=title, acquisition_datetime=datetime.now())
            for index, title in enumerate(("Pupillography", "Tear Film Analysis", "Meibography"))
        }
        db.add_all(studies.values())
        db.flush()

        for study_index, (title, study) in enumerate(studies.items()):
            for image_index, size in enumerate(((800, 600), (800, 600), (640, 480))):
                relative_path = f"reports/{study_index}-{image_index}.png"
                path = main.settings.storage_root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", size, (study_index * 30, image_index * 30, 20)).save(path)
                content = path.read_bytes()
                db.add(ClinicalFile(
                    study=study, kind="image", original_name=f"{study_index}-{image_index}.cso",
                    relative_path=relative_path, mime_type="image/png", size_bytes=len(content),
                    sha256=sha256(content).hexdigest(), role="Image in .cso",
                    embedded_path=f"{study_index}-{image_index}.cso",
                ))
        db.commit()
        study_ids = {title: study.id for title, study in studies.items()}

    headers = auth_headers(client)
    pupil = client.get(f"/api/studies/{study_ids['Pupillography']}/reports", headers=headers).json()
    tear_film = client.get(f"/api/studies/{study_ids['Tear Film Analysis']}/reports", headers=headers).json()
    meibography = client.get(f"/api/studies/{study_ids['Meibography']}/reports", headers=headers).json()

    assert len(pupil) == 2
    assert len(tear_film) == 2
    assert meibography == []


def test_phoenix_report_manifest_and_image_are_authenticated(client, monkeypatch):
    from datetime import datetime
    from hashlib import sha256
    from io import BytesIO
    from PIL import Image
    from app.models import ClinicalFile, DicomObject, Machine, Patient, Study

    with database.SessionLocal() as db:
        study = Study(
            study_uid="phoenix-pupil", patient=Patient(practo_uid="10044", name="Test Patient"),
            machine=Machine(code="REPORT-3", name="Sirius"), title="Pupillography",
            acquisition_datetime=datetime(2025, 12, 23, 14, 18),
        )
        relative_path = "original/source.dcm"
        source = main.settings.storage_root / relative_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"DICOM fixture")
        original = ClinicalFile(
            study=study, kind="dicom", original_name="source.dcm", relative_path=relative_path,
            mime_type="application/dicom", size_bytes=source.stat().st_size,
            sha256=sha256(source.read_bytes()).hexdigest(), role="Original DICOM object",
        )
        dicom = DicomObject(
            study=study, original_file=original, sop_uid="phoenix-pupil-object", laterality="L",
            zip_status="readable",
        )
        db.add(dicom)
        db.commit()
        study_id, object_id = study.id, dicom.id

    output = BytesIO()
    Image.new("RGB", (1600, 936), "white").save(output, "PNG")
    monkeypatch.setattr(main, "dicom_has_phoenix_report", lambda path, title: True)
    monkeypatch.setattr(main, "render_dicom_report", lambda *args, **kwargs: output.getvalue())

    manifest_path = f"/api/studies/{study_id}/phoenix-reports"
    image_path = f"{manifest_path}/{object_id}.png"
    assert client.get(manifest_path).status_code == 401
    assert client.get(image_path).status_code == 401

    headers = auth_headers(client)
    manifest = client.get(manifest_path, headers=headers)
    assert manifest.status_code == 200
    assert manifest.json() == [{
        "object_id": object_id,
        "label": "Left eye (OS)",
        "laterality": "L",
        "mime_type": "image/png",
        "url": image_path,
    }]
    image = client.get(image_path, headers=headers)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert Image.open(BytesIO(image.content)).size == (1600, 936)
