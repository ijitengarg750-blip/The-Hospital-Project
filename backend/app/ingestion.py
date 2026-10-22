from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import mimetypes
from pathlib import Path
import re
import shutil

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .dicom_import import classify_file, embedded_archives, inspect_dicom, safe_archive_members
from .models import ClinicalFile, CollectionEvent, DicomObject, EmbeddedEntry, Machine, Patient, Study


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name)[:180] or "file.bin"


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_new(source: Path, target: Path, created_paths: list[Path]) -> None:
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    created_paths.append(target)


def _write_new(target: Path, content: bytes, created_paths: list[Path]) -> None:
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    created_paths.append(target)


def _remove_created_files(created_paths: list[Path], root: Path) -> None:
    resolved_root = root.resolve()
    for path in reversed(created_paths):
        path.unlink(missing_ok=True)
        parent = path.parent
        while parent != resolved_root and resolved_root in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def _record_file(
    db: Session,
    study: Study,
    path: Path,
    kind: str,
    original_name: str,
    mime: str | None = None,
    role: str | None = None,
    embedded_path: str | None = None,
) -> ClinicalFile:
    digest = _sha256_file(path)
    existing = db.scalar(select(ClinicalFile).where(ClinicalFile.study_id == study.id, ClinicalFile.sha256 == digest))
    if existing:
        existing.role = existing.role or role
        existing.embedded_path = existing.embedded_path or embedded_path
        return existing
    root = get_settings().storage_root.resolve()
    item = ClinicalFile(
        study=study,
        kind=kind,
        original_name=original_name,
        relative_path=str(path.resolve().relative_to(root)),
        sha256=digest,
        mime_type=mime or mimetypes.guess_type(original_name)[0],
        size_bytes=path.stat().st_size,
        role=role,
        embedded_path=embedded_path,
    )
    db.add(item)
    db.flush()
    return item


def _generic_ingest(
    db: Session,
    machine: Machine,
    incoming_path: Path,
    original_name: str,
    practo_uid: str | None,
    supplied_study_uid: str | None,
    title: str | None,
    acquisition_datetime: datetime | None,
    created_paths: list[Path],
) -> Study:
    if not practo_uid:
        raise ValueError("A non-DICOM file requires practo_uid from the collector configuration")
    digest = _sha256_file(incoming_path)
    patient = db.scalar(select(Patient).where(Patient.practo_uid == practo_uid))
    if not patient:
        patient = Patient(practo_uid=practo_uid)
        db.add(patient)
        db.flush()
    study_uid = supplied_study_uid or f"{machine.code}:{digest}"
    study = db.scalar(select(Study).where(Study.study_uid == study_uid))
    if not study:
        study = Study(
            study_uid=study_uid, patient=patient, machine=machine,
            source_patient_id=practo_uid,
            title=title or machine.modality or "Diagnostic report",
            acquisition_datetime=acquisition_datetime or datetime.now(timezone.utc),
        )
        db.add(study)
        db.flush()
    elif study.patient_id != patient.id:
        raise ValueError("Study UID already belongs to a different patient")
    date_part = study.acquisition_datetime.strftime("%Y/%m/%d")
    folder = get_settings().storage_root / _safe_name(practo_uid) / date_part / _safe_name(study_uid) / "reports"
    suffix = Path(_safe_name(original_name)).suffix[:20]
    target = folder / f"{digest}{suffix}"
    _copy_new(incoming_path, target, created_paths)
    content = target.read_bytes()[:64]
    kind, mime = classify_file(original_name, content)
    if kind == "proprietary":
        kind = "report"
    _record_file(db, study, target, kind, original_name, mime, role="Machine export")
    return study


def _content_extension(member) -> str:
    if member.format == "JPEG":
        return ".jpg"
    if member.format == "PNG":
        return ".png"
    if member.format == "PDF":
        return ".pdf"
    if member.format == "XML candidate":
        return ".xml"
    suffix = Path(member.name).suffix
    return suffix[:12] if suffix and re.fullmatch(r"\.[A-Za-z0-9]+", suffix) else ".bin"


def ingest_upload(
    db: Session,
    machine_code: str,
    incoming_path: Path,
    original_name: str,
    practo_uid: str | None = None,
    supplied_study_uid: str | None = None,
    title: str | None = None,
    acquisition_datetime: datetime | None = None,
) -> Study:
    machine = db.scalar(select(Machine).where(Machine.code == machine_code))
    if not machine:
        raise ValueError(f"Unknown machine code: {machine_code}")
    if not machine.active:
        raise ValueError(f"Machine is inactive: {machine_code}")
    created_paths: list[Path] = []
    try:
        if incoming_path.suffix.lower() not in {".dcm", ".dicom"}:
            study = _generic_ingest(
                db, machine, incoming_path, original_name, practo_uid,
                supplied_study_uid, title, acquisition_datetime, created_paths,
            )
            db.add(CollectionEvent(machine_code=machine_code, filename=original_name, status="success", detail=study.study_uid))
            db.commit()
            db.refresh(study)
            return study

        ds, info = inspect_dicom(incoming_path)
        patient = db.scalar(select(Patient).where(Patient.practo_uid == info.patient_uid))
        if not patient:
            patient = Patient(practo_uid=info.patient_uid, name=info.patient_name, date_of_birth=info.date_of_birth)
            db.add(patient)
            db.flush()
        else:
            patient.name = patient.name or info.patient_name
            patient.date_of_birth = patient.date_of_birth or info.date_of_birth

        study = db.scalar(select(Study).where(Study.study_uid == info.study_uid))
        if not study:
            study = Study(
                study_uid=info.study_uid, patient=patient, machine=machine,
                source_patient_id=info.patient_uid, title=info.title,
                acquisition_datetime=info.acquisition_datetime,
            )
            db.add(study)
            db.flush()
        elif study.patient_id != patient.id:
            raise ValueError("Study UID already belongs to a different patient")
        elif study.machine_id != machine.id:
            raise ValueError("Study UID already belongs to a different machine")

        date_part = info.acquisition_datetime.strftime("%Y/%m/%d")
        study_folder = get_settings().storage_root / _safe_name(info.patient_uid) / date_part / _safe_name(info.study_uid)
        original_folder = study_folder / "original"
        extracted_folder = study_folder / "extracted"
        digest = _sha256_file(incoming_path)
        target = original_folder / f"{digest}.dcm"
        _copy_new(incoming_path, target, created_paths)
        original_file = _record_file(db, study, target, "dicom", original_name, "application/dicom", role="Original DICOM object")

        existing_object = db.scalar(select(DicomObject).where(DicomObject.sop_uid == info.sop_uid))
        if existing_object:
            db.add(CollectionEvent(machine_code=machine_code, filename=original_name, status="duplicate", detail=info.sop_uid))
            db.commit()
            db.refresh(study)
            return study

        dicom_object = DicomObject(
            study=study, original_file=original_file,
            series_uid=info.series_uid, sop_uid=info.sop_uid,
            series_number=info.series_number, instance_number=info.instance_number,
            acquisition_datetime=info.raw_acquisition_datetime,
            acquisition_time=info.acquisition_time, laterality=info.laterality,
            modality=info.modality, sop_class=info.sop_class,
            pixel_data_present=info.pixel_data_present, zip_status="none",
        )
        db.add(dicom_object)
        db.flush()

        archives = embedded_archives(ds)
        if archives:
            dicom_object.zip_status = "readable"
        for archive_index, archive in enumerate(archives, start=1):
            for member in safe_archive_members(archive.data):
                dicom_object.entry_count += 1
                if member.format in {"JPEG", "PNG"}:
                    dicom_object.image_count += 1
                else:
                    dicom_object.nonimage_count += 1
                stored_file = None
                if member.data is not None and member.sha256:
                    suffix = _content_extension(member)
                    destination = extracted_folder / member.sha256[:2] / f"{member.sha256}{suffix}"
                    _write_new(destination, member.data, created_paths)
                    kind, mime = classify_file(member.name, member.data[:64])
                    stored_file = _record_file(
                        db, study, destination, kind, member.name, mime,
                        role=member.role, embedded_path=member.name,
                    )
                entry = EmbeddedEntry(
                    dicom_object=dicom_object, file=stored_file,
                    archive_index=archive_index, private_tag=archive.private_tag,
                    entry_name=member.name, size_bytes=member.size_bytes,
                    compressed_bytes=member.compressed_bytes,
                    format=member.format, role=member.role, status=member.status,
                    sha256=member.sha256, nested_members=member.nested_members,
                    xml_elements=member.xml_elements, error=member.error,
                )
                db.add(entry)

        db.add(CollectionEvent(machine_code=machine_code, filename=original_name, status="success", detail=info.study_uid))
        db.commit()
        db.refresh(study)
        return study
    except Exception as exc:
        db.rollback()
        _remove_created_files(created_paths, get_settings().storage_root)
        db.add(CollectionEvent(machine_code=machine_code, filename=original_name, status="failed", detail=str(exc)))
        db.commit()
        raise
