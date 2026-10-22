from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .database import Base, engine, get_db
from .ingestion import ingest_upload
from .models import AuditLog, ClinicalFile, DicomObject, Machine, Patient, Study
from .schemas import LoginRequest, MachineCreate, MachineOut, PatientOut, StudyInventoryOut
from .security import authenticate, create_token, current_user, require_collector

settings = get_settings()
app = FastAPI(title="Hospital Diagnostic Dashboard API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def startup():
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc)}


@app.post("/api/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    if not authenticate(payload.username, payload.password):
        raise HTTPException(401, "Incorrect username or password")
    db.add(AuditLog(username=payload.username, action="login"))
    db.commit()
    return {"access_token": create_token(payload.username), "token_type": "bearer"}


@app.post("/api/machines", response_model=MachineOut)
def create_machine(payload: MachineCreate, user: str = Depends(current_user), db: Session = Depends(get_db)):
    if db.scalar(select(Machine).where(Machine.code == payload.code)):
        raise HTTPException(409, "Machine code already exists")
    machine = Machine(**payload.model_dump())
    db.add(machine)
    db.add(AuditLog(username=user, action="machine.create", resource=payload.code))
    db.commit()
    db.refresh(machine)
    return machine


@app.get("/api/machines", response_model=list[MachineOut])
def list_machines(_: str = Depends(current_user), db: Session = Depends(get_db)):
    return list(db.scalars(select(Machine).order_by(Machine.name)))


@app.get("/api/patients/{practo_uid}", response_model=PatientOut)
def patient_by_uid(practo_uid: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    stmt = (
        select(Patient)
        .where(Patient.practo_uid == practo_uid)
        .options(
            selectinload(Patient.studies).selectinload(Study.machine),
            selectinload(Patient.studies).selectinload(Study.files),
            selectinload(Patient.studies).selectinload(Study.dicom_objects).selectinload(DicomObject.entries),
        )
    )
    patient = db.scalar(stmt)
    if not patient:
        raise HTTPException(404, "No patient found with this Practo UID")
    patient.studies.sort(key=lambda x: x.acquisition_datetime, reverse=True)
    db.add(AuditLog(username=user, action="patient.view", resource=practo_uid))
    db.commit()
    return patient


@app.get("/api/files/{file_id}")
def view_file(file_id: int, user: str = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(ClinicalFile, file_id)
    if not item:
        raise HTTPException(404, "File not found")
    path = (settings.storage_root / item.relative_path).resolve()
    if settings.storage_root.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "Stored file is unavailable")
    db.add(AuditLog(username=user, action="file.view", resource=str(file_id)))
    db.commit()
    return FileResponse(path, media_type=item.mime_type or "application/octet-stream", filename=item.original_name, content_disposition_type="inline")


@app.get("/api/studies/{study_id}/inventory", response_model=StudyInventoryOut)
def study_inventory(study_id: int, user: str = Depends(current_user), db: Session = Depends(get_db)):
    study = db.scalar(
        select(Study)
        .where(Study.id == study_id)
        .options(selectinload(Study.dicom_objects).selectinload(DicomObject.entries))
    )
    if not study:
        raise HTTPException(404, "Study not found")
    objects = sorted(
        study.dicom_objects,
        key=lambda item: (item.series_number or "", item.series_uid or "", item.acquisition_datetime or "", item.sop_uid),
    )
    for item in objects:
        item.entries.sort(key=lambda entry: (entry.role, entry.entry_name))
    db.add(AuditLog(username=user, action="study.inventory", resource=str(study_id)))
    db.commit()
    return {"study_id": study.id, "study_uid": study.study_uid, "dicom_objects": objects}


@app.post("/api/studies/{study_id}/remote-session")
def remote_session(study_id: int, user: str = Depends(current_user), db: Session = Depends(get_db)):
    study = db.scalar(select(Study).where(Study.id == study_id).options(selectinload(Study.machine)))
    if not study or not study.machine.no_vnc_url:
        raise HTTPException(404, "Remote access is not configured for this machine")
    db.add(AuditLog(username=user, action="remote.open", resource=f"study:{study_id};machine:{study.machine.code}"))
    db.commit()
    return {"url": study.machine.no_vnc_url}


@app.post("/api/collector/upload", dependencies=[Depends(require_collector)])
def collector_upload(
    machine_code: str = Form(...),
    file: UploadFile = File(...),
    practo_uid: str | None = Form(default=None),
    study_uid: str | None = Form(default=None),
    title: str | None = Form(default=None),
    acquisition_datetime: datetime | None = Form(default=None),
    db: Session = Depends(get_db),
):
    suffix = Path(file.filename or "upload.dcm").suffix
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="hospital-upload-", suffix=suffix, delete=False) as temp:
            temp_path = Path(temp.name)
            written = 0
            while chunk := file.file.read(1024 * 1024):
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise HTTPException(413, "Upload exceeds configured size limit")
                temp.write(chunk)
        study = ingest_upload(
            db,
            machine_code,
            temp_path,
            file.filename or "upload.dcm",
            practo_uid=practo_uid,
            supplied_study_uid=study_uid,
            title=title,
            acquisition_datetime=acquisition_datetime,
        )
        return {"status": "accepted", "study_id": study.id, "study_uid": study.study_uid}
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)

