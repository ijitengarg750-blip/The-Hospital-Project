from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import zipfile

import pytest
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import ingestion
from app.config import Settings
from app.database import Base
from app.dicom_import import inspect_dicom, safe_archive_members
from app.models import ClinicalFile, DicomObject, Machine, Patient, Study


def write_dicom(path: Path, *, patient_id: str = "10042", study_uid: str | None = None, sop_uid: str | None = None) -> tuple[str, str]:
    meta = Dataset()
    meta.MediaStorageSOPClassUID = generate_uid()
    meta.MediaStorageSOPInstanceUID = sop_uid or generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    dataset = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    dataset.PatientID = patient_id
    dataset.PatientName = "Test^Patient"
    dataset.StudyInstanceUID = study_uid or generate_uid()
    dataset.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    dataset.SOPClassUID = meta.MediaStorageSOPClassUID
    dataset.StudyDate = "20261022"
    dataset.StudyTime = "090000"
    dataset.Modality = "OP"
    dataset.save_as(path, enforce_file_format=True)
    return dataset.StudyInstanceUID, dataset.SOPInstanceUID


@pytest.fixture
def database(tmp_path, monkeypatch):
    storage = tmp_path / "clinical-data"
    monkeypatch.setattr(ingestion, "get_settings", lambda: Settings(storage_root=storage))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add(Machine(code="TEST-1", name="Disposable test machine", active=True))
        db.commit()
        yield db, storage
    Base.metadata.drop_all(engine)


def test_same_dicom_under_different_filenames_is_one_object_and_one_stored_asset(database, tmp_path):
    db, storage = database
    source = tmp_path / "source.dcm"
    write_dicom(source)

    first = ingestion.ingest_upload(db, "TEST-1", source, "first-name.dcm")
    second = ingestion.ingest_upload(db, "TEST-1", source, "renamed-copy.dcm")
    third = ingestion.ingest_upload(db, "TEST-1", source, "first-name.dcm")

    assert first.id == second.id
    assert first.id == third.id
    assert db.scalar(select(DicomObject).where(DicomObject.study_id == first.id)).sop_uid
    assert len(list(db.scalars(select(ClinicalFile).where(ClinicalFile.study_id == first.id)))) == 1
    assert len(list((storage / "10042").rglob("*.dcm"))) == 1


def test_distinct_studies_for_one_patient_are_preserved(database, tmp_path):
    db, _ = database
    one = tmp_path / "one.dcm"
    two = tmp_path / "two.dcm"
    write_dicom(one)
    write_dicom(two)

    ingestion.ingest_upload(db, "TEST-1", one, "one.dcm")
    ingestion.ingest_upload(db, "TEST-1", two, "two.dcm")

    patient = db.scalar(select(Patient).where(Patient.practo_uid == "10042"))
    assert len(list(db.scalars(select(Study).where(Study.patient_id == patient.id)))) == 2


def test_rejects_same_study_uid_with_a_conflicting_patient(database, tmp_path):
    db, _ = database
    first = tmp_path / "first.dcm"
    study_uid, _ = write_dicom(first, patient_id="10042")
    second = tmp_path / "second.dcm"
    write_dicom(second, patient_id="20042", study_uid=study_uid)
    ingestion.ingest_upload(db, "TEST-1", first, "first.dcm")

    with pytest.raises(ValueError, match="belongs to a different patient"):
        ingestion.ingest_upload(db, "TEST-1", second, "second.dcm")


def test_rejects_an_inactive_machine(database, tmp_path):
    db, _ = database
    machine = db.scalar(select(Machine).where(Machine.code == "TEST-1"))
    machine.active = False
    db.commit()
    source = tmp_path / "source.dcm"
    write_dicom(source)

    with pytest.raises(ValueError, match="inactive"):
        ingestion.ingest_upload(db, "TEST-1", source, "source.dcm")


def test_removes_new_files_when_ingestion_fails_after_copy(database, tmp_path, monkeypatch):
    db, storage = database
    source = tmp_path / "source.dcm"
    write_dicom(source)
    monkeypatch.setattr(ingestion, "embedded_archives", lambda _dataset: (_ for _ in ()).throw(RuntimeError("injected failure")))

    with pytest.raises(RuntimeError, match="injected failure"):
        ingestion.ingest_upload(db, "TEST-1", source, "source.dcm")

    assert not list(storage.rglob("*"))
    assert not list(db.scalars(select(Study)))


def test_original_dicom_content_hash_matches_the_source(database, tmp_path):
    db, storage = database
    source = tmp_path / "source.dcm"
    write_dicom(source)
    study = ingestion.ingest_upload(db, "TEST-1", source, "source.dcm")

    record = db.scalar(select(ClinicalFile).where(ClinicalFile.study_id == study.id))
    stored = storage / record.relative_path
    assert record.sha256 == sha256(source.read_bytes()).hexdigest()
    assert sha256(stored.read_bytes()).hexdigest() == record.sha256


def test_partial_private_archive_is_rejected_without_study_or_stored_files(database, tmp_path):
    from pydicom import dcmread

    db, storage = database
    source = tmp_path / "incomplete.dcm"
    write_dicom(source)
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("Scotopic/eye.jpg", b"\xff\xd8\xff" + b"image fixture" * 200)
    dataset = dcmread(source)
    dataset.add_new((0x7001, 0x1099), "OB", archive.getvalue())
    dataset.save_as(source, enforce_file_format=True)
    source.write_bytes(source.read_bytes()[:-100])

    with pytest.raises(zipfile.BadZipFile):
        ingestion.ingest_upload(db, "TEST-1", source, "incomplete.dcm")

    assert not list(db.scalars(select(Study)))
    assert not list(storage.rglob("*.dcm"))
    assert not list(db.scalars(select(ClinicalFile)))


def test_archive_skips_posix_and_windows_traversal_names():
    blob = BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        archive.writestr("../outside.txt", b"no")
        archive.writestr("..\\outside-windows.txt", b"no")
        archive.writestr("C:\\absolute-windows.txt", b"no")
        archive.writestr("safe/report.txt", b"yes")

    assert [member.name for member in safe_archive_members(blob.getvalue())] == ["safe/report.txt"]


def test_archive_skips_high_compression_ratio_members():
    blob = BytesIO()
    with zipfile.ZipFile(blob, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("compressed.txt", b"0" * 100_000)

    member = next(safe_archive_members(blob.getvalue()))
    assert member.data is None
    assert member.status == "unreadable/skipped"
    assert member.error == "archive member exceeds compression-ratio safety limit"


def test_dicom_without_patient_id_is_rejected(tmp_path):
    source = tmp_path / "missing-patient-id.dcm"
    write_dicom(source)
    from pydicom import dcmread

    dataset = dcmread(source)
    del dataset.PatientID
    dataset.save_as(source, enforce_file_format=True)

    with pytest.raises(ValueError, match="missing PatientID"):
        inspect_dicom(source)
