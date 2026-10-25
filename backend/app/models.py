from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[int] = mapped_column(primary_key=True)
    practo_uid: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    studies: Mapped[list[Study]] = relationship(back_populates="patient", cascade="all, delete-orphan")


class Machine(Base):
    __tablename__ = "machines"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    modality: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(255))
    no_vnc_url: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    studies: Mapped[list[Study]] = relationship(back_populates="machine")


class Study(Base):
    __tablename__ = "studies"
    id: Mapped[int] = mapped_column(primary_key=True)
    study_uid: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    source_patient_id: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), default="Diagnostic examination")
    acquisition_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(50), default="available")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    patient: Mapped[Patient] = relationship(back_populates="studies")
    machine: Mapped[Machine] = relationship(back_populates="studies")
    files: Mapped[list[ClinicalFile]] = relationship(back_populates="study", cascade="all, delete-orphan")
    dicom_objects: Mapped[list[DicomObject]] = relationship(back_populates="study", cascade="all, delete-orphan")

    @property
    def dicom_objects_count(self) -> int:
        return len(self.dicom_objects)

    @property
    def series_count(self) -> int:
        return len({item.series_uid for item in self.dicom_objects if item.series_uid})

    @property
    def embedded_entries_count(self) -> int:
        return sum(len(item.entries) for item in self.dicom_objects)

    @property
    def image_entries_count(self) -> int:
        return sum(1 for item in self.dicom_objects for entry in item.entries if entry.format in {"JPEG", "PNG"})

    @property
    def unique_images_count(self) -> int:
        return len({entry.sha256 for item in self.dicom_objects for entry in item.entries if entry.format in {"JPEG", "PNG"} and entry.sha256})

    @property
    def pixel_data_objects_count(self) -> int:
        return sum(1 for item in self.dicom_objects if item.pixel_data_present)


class ClinicalFile(Base):
    __tablename__ = "clinical_files"
    __table_args__ = (UniqueConstraint("study_id", "sha256"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    original_name: Mapped[str] = mapped_column(String(500))
    relative_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    role: Mapped[str | None] = mapped_column(String(255))
    embedded_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    study: Mapped[Study] = relationship(back_populates="files")
    entries: Mapped[list[EmbeddedEntry]] = relationship(back_populates="file")


class DicomObject(Base):
    __tablename__ = "dicom_objects"
    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id"), index=True)
    original_file_id: Mapped[int] = mapped_column(ForeignKey("clinical_files.id"), unique=True)
    series_uid: Mapped[str | None] = mapped_column(String(255), index=True)
    sop_uid: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    series_number: Mapped[str | None] = mapped_column(String(50))
    instance_number: Mapped[str | None] = mapped_column(String(50))
    acquisition_datetime: Mapped[str | None] = mapped_column(String(80))
    acquisition_time: Mapped[str | None] = mapped_column(String(80))
    laterality: Mapped[str | None] = mapped_column(String(20))
    modality: Mapped[str | None] = mapped_column(String(50))
    sop_class: Mapped[str | None] = mapped_column(String(255))
    pixel_data_present: Mapped[bool] = mapped_column(Boolean, default=False)
    zip_status: Mapped[str] = mapped_column(String(255), default="none")
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    nonimage_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    study: Mapped[Study] = relationship(back_populates="dicom_objects")
    original_file: Mapped[ClinicalFile] = relationship(foreign_keys=[original_file_id])
    entries: Mapped[list[EmbeddedEntry]] = relationship(back_populates="dicom_object", cascade="all, delete-orphan")


class EmbeddedEntry(Base):
    __tablename__ = "embedded_entries"
    __table_args__ = (UniqueConstraint("dicom_object_id", "archive_index", "entry_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dicom_object_id: Mapped[int] = mapped_column(ForeignKey("dicom_objects.id"), index=True)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("clinical_files.id"), index=True)
    archive_index: Mapped[int] = mapped_column(Integer)
    private_tag: Mapped[str | None] = mapped_column(String(40))
    entry_name: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    compressed_bytes: Mapped[int | None] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    nested_members: Mapped[int | None] = mapped_column(Integer)
    xml_elements: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    dicom_object: Mapped[DicomObject] = relationship(back_populates="entries")
    file: Mapped[ClinicalFile | None] = relationship(back_populates="entries")


class CollectionEvent(Base):
    __tablename__ = "collection_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    machine_code: Mapped[str] = mapped_column(String(100), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(50), index=True)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthAttempt(Base):
    __tablename__ = "auth_attempts"
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PatientMapping(Base):
    __tablename__ = "patient_mappings"
    __table_args__ = (UniqueConstraint("machine_id", "source_patient_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    source_patient_id: Mapped[str] = mapped_column(String(255))
    practo_uid: Mapped[str] = mapped_column(String(100), index=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)

