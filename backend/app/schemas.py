from datetime import date, datetime
from typing import Literal
from urllib.parse import parse_qsl, urlsplit
from pydantic import BaseModel, ConfigDict, field_validator


class LoginRequest(BaseModel):
    username: str
    password: str


class RemoteAccessUpdate(BaseModel):
    no_vnc_url: str | None

    @field_validator("no_vnc_url")
    @classmethod
    def safe_remote_url(cls, value):
        if value is None or not value.strip():
            return None
        value = value.strip()
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or "\\" in value or any(c.isspace() for c in value):
            raise ValueError("Use a complete HTTP or HTTPS noVNC URL")
        if url.username or url.password or url.fragment or any(key.lower() in {"password", "passwd", "token", "username"} for key, _ in parse_qsl(url.query)):
            raise ValueError("Enter credentials in noVNC, never in the URL")
        return value


class MachineCreate(RemoteAccessUpdate):
    code: str
    name: str
    modality: str | None = None
    location: str | None = None
    no_vnc_url: str | None = None


class ReportTypeUpdate(BaseModel):
    report_type: Literal["Corneal mapping summary", "Corneal thickness", "Tear-film analysis", "Pupillography"] | None


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    original_name: str
    mime_type: str | None
    size_bytes: int
    role: str | None
    embedded_path: str | None


class MachineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    modality: str | None
    location: str | None
    no_vnc_url: str | None


class StudyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    study_uid: str
    title: str
    acquisition_datetime: datetime
    status: str
    dicom_objects_count: int
    series_count: int
    embedded_entries_count: int
    image_entries_count: int
    unique_images_count: int
    pixel_data_objects_count: int
    machine: MachineOut
    files: list[FileOut]


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    practo_uid: str
    name: str | None
    date_of_birth: date | None
    studies: list[StudyOut]


class EmbeddedEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    archive_index: int
    private_tag: str | None
    entry_name: str
    size_bytes: int
    compressed_bytes: int | None
    format: str
    role: str
    status: str
    sha256: str | None
    nested_members: int | None
    xml_elements: str | None
    error: str | None


class DicomObjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    series_uid: str | None
    sop_uid: str
    series_number: str | None
    instance_number: str | None
    acquisition_datetime: str | None
    acquisition_time: str | None
    laterality: str | None
    modality: str | None
    sop_class: str | None
    pixel_data_present: bool
    zip_status: str
    entry_count: int
    image_count: int
    nonimage_count: int
    error: str | None
    entries: list[EmbeddedEntryOut]


class StudyInventoryOut(BaseModel):
    study_id: int
    study_uid: str
    dicom_objects: list[DicomObjectOut]

