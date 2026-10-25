from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import tempfile

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response as FastAPIResponse, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .database import Base, engine, get_db
from .ingestion import ingest_upload
from .dicom_import import role_for_member
from .models import AuditLog, AuthAttempt, ClinicalFile, DicomObject, Machine, Patient, Study, utcnow
from .phoenix_reports import PhoenixReportError, dicom_has_phoenix_report, render_dicom_report
from .remote_sessions import router as remote_sessions_router
from .machine_admin import MachinePermission, require_machine_access, router as machine_admin_router
from .vnc_gateway import router as vnc_gateway_router
from .schemas import FileOut, LoginRequest, MachineOut, PatientOut, PhoenixReportOut, RemoteAccessUpdate, ReportTypeUpdate, StudyInventoryOut
from .security import AuthenticatedUser, authenticate, create_token, current_user, require_admin, require_collector

settings = get_settings()
app = FastAPI(title="Hospital Diagnostic Dashboard API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(remote_sessions_router)
app.include_router(machine_admin_router)
app.include_router(vnc_gateway_router)


@app.on_event("startup")
def startup():
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc)}


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: FastAPIResponse, db: Session = Depends(get_db)):
    now = utcnow()
    client_host = request.client.host if request.client else "unknown"
    key_hash = sha256(f"{client_host}:{payload.username.casefold()}".encode()).hexdigest()
    attempt = db.scalar(select(AuthAttempt).where(AuthAttempt.key_hash == key_hash).with_for_update())
    window = timedelta(minutes=settings.auth_window_minutes)
    if attempt and now - (attempt.window_started_at if attempt.window_started_at.tzinfo else attempt.window_started_at.replace(tzinfo=timezone.utc)) >= window:
        db.delete(attempt)
        db.flush()
        attempt = None
    if attempt and attempt.failures >= settings.auth_max_attempts:
        raise HTTPException(429, "Too many sign-in attempts", headers={"Retry-After": str(settings.auth_window_minutes * 60)})
    user = authenticate(payload.username, payload.password)
    if not user:
        if attempt:
            attempt.failures += 1
        else:
            db.add(AuthAttempt(key_hash=key_hash, failures=1, window_started_at=now))
        db.commit()
        raise HTTPException(401, "Incorrect username or password")
    if attempt:
        db.delete(attempt)
    response.set_cookie(
        settings.session_cookie_name,
        create_token(user),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        max_age=settings.access_token_minutes * 60,
        path="/",
    )
    db.add(AuditLog(username=str(user), action="login", resource=f"role:{user.role};amr:pwd"))
    db.commit()
    return {"username": str(user), "role": user.role, "mfa_required": settings.mfa_required}


@app.post("/api/auth/logout", status_code=204)
def logout(response: FastAPIResponse):
    response.delete_cookie(settings.session_cookie_name, path="/")


@app.get("/api/auth/session")
def auth_session(user: AuthenticatedUser = Depends(current_user)):
    return {"username": str(user), "role": user.role, "mfa_required": settings.mfa_required}


@app.patch("/api/machines/{machine_id}/remote-access", response_model=MachineOut)
def update_remote_access(machine_id: int, payload: RemoteAccessUpdate, user: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db)):
    machine = db.get(Machine, machine_id)
    if not machine:
        raise HTTPException(404, "Machine not found")
    machine.no_vnc_url = payload.no_vnc_url
    db.add(AuditLog(username=user, action="machine.remote.configure", resource=machine.code))
    db.commit()
    return machine


@app.patch("/api/files/{file_id}/report-type", response_model=FileOut)
def update_report_type(file_id: int, payload: ReportTypeUpdate, user: AuthenticatedUser = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(ClinicalFile, file_id)
    if not item:
        raise HTTPException(404, "File not found")
    require_machine_access(user, item.study.machine_id, db)
    if item.kind not in {"image", "report_image"} or item.mime_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(422, "Only JPEG or PNG images can be selected as report images")
    item.kind = "report_image" if payload.report_type else "image"
    item.role = payload.report_type or (role_for_member(item.embedded_path, "JPEG" if item.mime_type == "image/jpeg" else "PNG") if item.embedded_path else "Machine export")
    db.add(AuditLog(username=user, action="file.report.select", resource=f"file:{file_id};type:{payload.report_type or 'source'}"))
    db.commit()
    return item


@app.get("/api/patients/{practo_uid}", response_model=PatientOut)
def patient_by_uid(practo_uid: str, user: AuthenticatedUser = Depends(current_user), db: Session = Depends(get_db)):
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
    result = PatientOut.model_validate(patient)
    if user.role != "admin":
        allowed_machine_ids = set(db.scalars(select(MachinePermission.machine_id).where(
            MachinePermission.doctor_username == str(user)
        )))
        result.studies = [study for study in result.studies if study.machine.id in allowed_machine_ids]
        if not result.studies:
            raise HTTPException(404, "No patient found with this Practo UID")
    db.add(AuditLog(username=user, action="patient.view", resource=practo_uid))
    db.commit()
    return result


@app.get("/api/files/{file_id}")
def view_file(file_id: int, user: AuthenticatedUser = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(ClinicalFile, file_id)
    if not item:
        raise HTTPException(404, "File not found")
    require_machine_access(user, item.study.machine_id, db)
    path = (settings.storage_root / item.relative_path).resolve()
    if settings.storage_root.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "Stored file is unavailable")
    db.add(AuditLog(username=user, action="file.view", resource=str(file_id)))
    db.commit()
    return FileResponse(path, media_type=item.mime_type or "application/octet-stream", filename=item.original_name, content_disposition_type="inline")


def _is_automatic_report_image(study: Study, item: ClinicalFile) -> bool:
    if item.kind != "image" or item.mime_type not in {"image/jpeg", "image/png"}:
        return False
    path = (settings.storage_root / item.relative_path).resolve()
    if settings.storage_root.resolve() not in path.parents or not path.is_file():
        return False
    try:
        with Image.open(path) as image:
            width, height = image.size
        if width >= 1000 and width / height >= 1.5:
            return True
        return (
            study.title.casefold() in {"pupillography", "tear film analysis"}
            and (width, height) == (800, 600)
            and item.role == "Image in .cso"
        )
    except (OSError, UnidentifiedImageError):
        return False


@app.get("/api/studies/{study_id}/reports", response_model=list[FileOut])
def study_reports(study_id: int, user: AuthenticatedUser = Depends(current_user), db: Session = Depends(get_db)):
    study = db.scalar(select(Study).where(Study.id == study_id).options(selectinload(Study.files)))
    if not study:
        raise HTTPException(404, "Study not found")
    require_machine_access(user, study.machine_id, db)
    reports = [item for item in study.files if item.kind in {"report", "report_image"}]
    reports.extend(item for item in study.files if item not in reports and _is_automatic_report_image(study, item))
    reports.sort(key=lambda item: (item.kind not in {"report", "report_image"}, item.id))
    db.add(AuditLog(username=user, action="report.gallery.view", resource=f"study:{study_id}"))
    db.commit()
    return reports[:4]


def _stored_path(item: ClinicalFile) -> Path | None:
    path = (settings.storage_root / item.relative_path).resolve()
    if settings.storage_root.resolve() not in path.parents or not path.is_file():
        return None
    return path


def _eye_name(laterality: str | None) -> str:
    if (laterality or "").upper() == "L":
        return "Left eye (OS)"
    if (laterality or "").upper() == "R":
        return "Right eye (OD)"
    return "Eye examination"


@app.get("/api/studies/{study_id}/phoenix-reports", response_model=list[PhoenixReportOut])
def phoenix_report_manifest(study_id: int, user: AuthenticatedUser = Depends(current_user), db: Session = Depends(get_db)):
    study = db.scalar(
        select(Study)
        .where(Study.id == study_id)
        .options(selectinload(Study.dicom_objects).selectinload(DicomObject.original_file))
    )
    if not study:
        raise HTTPException(404, "Study not found")
    require_machine_access(user, study.machine_id, db)
    reports = []
    for item in sorted(study.dicom_objects, key=lambda value: (value.series_number or "", value.id)):
        path = _stored_path(item.original_file)
        if path and dicom_has_phoenix_report(path, study.title):
            reports.append(PhoenixReportOut(
                object_id=item.id,
                label=_eye_name(item.laterality),
                laterality=item.laterality,
                url=f"/api/studies/{study.id}/phoenix-reports/{item.id}.png",
            ))
    db.add(AuditLog(username=user, action="phoenix.report.list", resource=f"study:{study_id}"))
    db.commit()
    return reports


@app.get("/api/studies/{study_id}/phoenix-reports/{object_id}.png")
def phoenix_report_image(study_id: int, object_id: int, user: AuthenticatedUser = Depends(current_user), db: Session = Depends(get_db)):
    study = db.scalar(select(Study).where(Study.id == study_id).options(selectinload(Study.patient)))
    item = db.scalar(
        select(DicomObject)
        .where(DicomObject.id == object_id, DicomObject.study_id == study_id)
        .options(selectinload(DicomObject.original_file))
    )
    if not study or not item:
        raise HTTPException(404, "Phoenix report source not found")
    require_machine_access(user, study.machine_id, db)
    path = _stored_path(item.original_file)
    if not path:
        raise HTTPException(404, "Stored DICOM source is unavailable")
    acquired = study.acquisition_datetime.strftime("%m/%d/%Y %I:%M %p").replace(" 0", " ")
    dob = study.patient.date_of_birth
    birthdate = f"{dob.month}/{dob.day}/{dob.year}" if dob else ""
    try:
        content = render_dicom_report(
            path, study.title, patient_name=study.patient.name or "Name unavailable",
            patient_id=study.patient.practo_uid, acquired=acquired, birthdate=birthdate,
        )
    except PhoenixReportError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.add(AuditLog(username=user, action="phoenix.report.view", resource=f"study:{study_id};object:{object_id}"))
    db.commit()
    return Response(
        content=content,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="phoenix-{study_id}-{object_id}.png"'},
    )


@app.get("/api/studies/{study_id}/inventory", response_model=StudyInventoryOut)
def study_inventory(study_id: int, user: AuthenticatedUser = Depends(current_user), db: Session = Depends(get_db)):
    study = db.scalar(
        select(Study)
        .where(Study.id == study_id)
        .options(selectinload(Study.dicom_objects).selectinload(DicomObject.entries))
    )
    if not study:
        raise HTTPException(404, "Study not found")
    require_machine_access(user, study.machine_id, db)
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
def remote_session(study_id: int, user: AuthenticatedUser = Depends(current_user), db: Session = Depends(get_db)):
    study = db.scalar(select(Study).where(Study.id == study_id).options(selectinload(Study.machine)))
    if not study or not study.machine.active or not study.machine.no_vnc_url:
        raise HTTPException(404, "Remote access is not configured for this machine")
    require_machine_access(user, study.machine_id, db)
    try:
        remote_url = RemoteAccessUpdate(no_vnc_url=study.machine.no_vnc_url).no_vnc_url
    except ValueError:
        raise HTTPException(422, "Update this machine's noVNC URL in remote access setup")
    db.add(AuditLog(username=user, action="remote.open", resource=f"study:{study_id};machine:{study.machine.code}"))
    db.commit()
    return {"url": remote_url}


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

