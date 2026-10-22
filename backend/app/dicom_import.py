from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from io import BytesIO
import mimetypes
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET
import zipfile

import pydicom

MAX_MEMBER_BYTES = 50_000_000
MAX_ARCHIVE_BYTES = 500_000_000
MAX_NESTED_BYTES = 50_000_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 500_000_000
MAX_COMPRESSION_RATIO = 100


@dataclass
class DicomInfo:
    patient_uid: str
    patient_name: str | None
    date_of_birth: date | None
    study_uid: str
    title: str
    acquisition_datetime: datetime
    modality: str | None
    series_uid: str | None
    sop_uid: str
    series_number: str | None
    instance_number: str | None
    raw_acquisition_datetime: str | None
    acquisition_time: str | None
    laterality: str | None
    sop_class: str | None
    pixel_data_present: bool


@dataclass
class EmbeddedArchive:
    private_tag: str
    data: bytes


@dataclass
class ArchiveMember:
    name: str
    data: bytes | None
    size_bytes: int
    compressed_bytes: int
    format: str
    role: str
    status: str
    sha256: str | None = None
    nested_members: int | None = None
    xml_elements: str | None = None
    error: str | None = None


def _clean(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dicom_datetime(ds) -> datetime:
    combined = _clean(getattr(ds, "AcquisitionDateTime", None))
    if combined:
        digits = re.sub(r"[^0-9.]", "", combined)
        try:
            return datetime.strptime(digits.split(".")[0][:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    d = _clean(getattr(ds, "StudyDate", None)) or _clean(getattr(ds, "ContentDate", None))
    t = _clean(getattr(ds, "StudyTime", None)) or _clean(getattr(ds, "ContentTime", None)) or "000000"
    if d:
        try:
            return datetime.strptime(d + re.sub(r"\D", "", t)[:6].ljust(6, "0"), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def inspect_dicom(path: Path) -> tuple[object, DicomInfo]:
    ds = pydicom.dcmread(path, force=True, stop_before_pixels=False, defer_size=1024 * 1024)
    if not getattr(ds, "SOPClassUID", None) and not getattr(ds, "StudyInstanceUID", None):
        raise ValueError("Not a recognizable DICOM dataset")
    patient_uid = _clean(getattr(ds, "PatientID", None))
    if not patient_uid:
        raise ValueError("DICOM is missing PatientID; it cannot be attached safely to a Practo UID")
    dob = None
    raw_dob = _clean(getattr(ds, "PatientBirthDate", None))
    if raw_dob:
        try:
            dob = datetime.strptime(raw_dob, "%Y%m%d").date()
        except ValueError:
            pass
    name = _clean(getattr(ds, "PatientName", None))
    if name:
        parts = [part for part in name.replace("^", " ").split() if part]
        if parts and parts[0] == patient_uid:
            parts = parts[1:]
        name = " ".join(parts) or None
    file_digest = _sha256_file(path)
    study_uid = _clean(getattr(ds, "StudyInstanceUID", None)) or f"missing-study:{file_digest}"
    sop_uid = _clean(getattr(ds, "SOPInstanceUID", None)) or f"missing-object:{file_digest}"
    title = _clean(getattr(ds, "StudyDescription", None)) or _clean(getattr(ds, "SeriesDescription", None)) or "Diagnostic examination"
    return ds, DicomInfo(
        patient_uid=patient_uid,
        patient_name=name,
        date_of_birth=dob,
        study_uid=study_uid,
        title=title,
        acquisition_datetime=_dicom_datetime(ds),
        modality=_clean(getattr(ds, "Modality", None)),
        series_uid=_clean(getattr(ds, "SeriesInstanceUID", None)),
        sop_uid=sop_uid,
        series_number=_clean(getattr(ds, "SeriesNumber", None)),
        instance_number=_clean(getattr(ds, "InstanceNumber", None)),
        raw_acquisition_datetime=_clean(getattr(ds, "AcquisitionDateTime", None)),
        acquisition_time=_clean(getattr(ds, "AcquisitionTime", None)),
        laterality=_clean(getattr(ds, "Laterality", None)) or _clean(getattr(ds, "ImageLaterality", None)),
        sop_class=_clean(getattr(ds, "SOPClassUID", None)),
        pixel_data_present="PixelData" in ds,
    )


def detect_format(data: bytes, name: str = "") -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"%PDF"):
        return "PDF"
    if data.startswith(b"PK\x03\x04"):
        return "ZIP"
    if data.lstrip(b"\xef\xbb\xbf\x00\r\n \t").startswith((b"<?xml", b"<ExamInfo", b"<P4")):
        return "XML candidate"
    if name.lower().endswith((".mp4", ".webm", ".avi", ".mov", ".mpeg")):
        return "Video"
    return "Unknown binary"


def role_for_member(name: str, detected_format: str) -> str:
    normalized = name.replace("\\", "/").lower()
    parts = [part for part in normalized.split("/") if part]
    if "dynamic" in parts:
        return "Dynamic sequence (ordered filename; not a timecode)"
    for category in ("mesopic", "photopic", "scotopic"):
        if category in parts:
            return f"{category.capitalize()} images"
    if normalized.endswith(".tmb"):
        return "Preview candidate (.tmb)"
    if normalized.endswith(".cso"):
        return "Image in .cso" if detected_format in {"JPEG", "PNG"} else "Proprietary .cso"
    if detected_format == "PDF":
        return "Report document"
    if detected_format == "Video":
        return "Browser video candidate"
    if detected_format == "XML candidate":
        return "XML metadata (values not clinically interpreted)"
    if detected_format in {"JPEG", "PNG"}:
        return "Other images / possible report or preview"
    if detected_format == "ZIP":
        return "Nested archive"
    return "Undecoded / proprietary"


def classify_file(name: str, data: bytes) -> tuple[str, str]:
    detected = detect_format(data, name)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if detected == "PDF":
        return "report", "application/pdf"
    if detected in {"JPEG", "PNG"}:
        return "image", "image/jpeg" if detected == "JPEG" else "image/png"
    if detected == "Video":
        return "video", mime
    if detected == "XML candidate":
        return "metadata", "application/xml"
    if detected == "ZIP":
        return "nested_archive", "application/zip"
    return "proprietary", mime


def embedded_archives(ds) -> list[EmbeddedArchive]:
    results: list[EmbeddedArchive] = []
    preferred = [(0x7001, 0x1099), (0x7001, 0x1098)]
    seen: set[tuple[int, int]] = set()
    for tag in preferred:
        if tag in ds:
            seen.add(tag)
            value = ds[tag].value
            if isinstance(value, bytes):
                offset = value.find(b"PK\x03\x04", 0, 8)
                if offset >= 0 and len(value) - offset <= MAX_ARCHIVE_BYTES:
                    results.append(EmbeddedArchive(f"({tag[0]:04X},{tag[1]:04X})", value[offset:]))
    for element in ds.iterall():
        tag = (int(element.tag.group), int(element.tag.element))
        if tag in seen or not isinstance(element.value, bytes):
            continue
        offset = element.value.find(b"PK\x03\x04", 0, 8)
        if offset >= 0 and len(element.value) - offset <= MAX_ARCHIVE_BYTES:
            results.append(EmbeddedArchive(f"({tag[0]:04X},{tag[1]:04X})", element.value[offset:]))
    return results


def _xml_element_names(data: bytes) -> str | None:
    try:
        root = ET.fromstring(data.lstrip(b"\x00"))
        return " | ".join(str(node.tag)[:80] for node in root.iter())[:1500]
    except Exception:
        return None


def safe_archive_members(blob: bytes):
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        total_uncompressed = 0
        for info in archive.infolist():
            posix = PurePosixPath(info.filename.replace("\\", "/"))
            if info.is_dir() or posix.is_absolute() or ".." in posix.parts:
                continue
            if re.match(r"^[A-Za-z]:", str(posix)):
                continue
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                yield ArchiveMember(
                    name=str(posix), data=None, size_bytes=info.file_size,
                    compressed_bytes=info.compress_size, format="Unknown binary",
                    role="Undecoded / proprietary", status="unreadable/skipped",
                    error="archive exceeds total uncompressed safety limit",
                )
                return
            if info.file_size and info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO:
                yield ArchiveMember(
                    name=str(posix), data=None, size_bytes=info.file_size,
                    compressed_bytes=info.compress_size, format="Unknown binary",
                    role="Undecoded / proprietary", status="unreadable/skipped",
                    error="archive member exceeds compression-ratio safety limit",
                )
                continue
            if info.file_size > MAX_MEMBER_BYTES:
                yield ArchiveMember(
                    name=str(posix), data=None, size_bytes=info.file_size,
                    compressed_bytes=info.compress_size, format="Unknown binary",
                    role="Undecoded / proprietary", status="unreadable/skipped",
                    error="exceeds per-entry safety limit",
                )
                continue
            try:
                with archive.open(info) as stream:
                    data = stream.read(MAX_MEMBER_BYTES + 1)
                if len(data) > MAX_MEMBER_BYTES:
                    raise ValueError("exceeds read limit")
                detected = detect_format(data, info.filename)
                role = role_for_member(info.filename, detected)
                digest = sha256(data).hexdigest()
                nested_members = None
                xml_elements = None
                status = "unsupported/proprietary"
                stored_data: bytes | None = data
                if detected in {"JPEG", "PNG"}:
                    status = "viewable image"
                elif detected == "PDF":
                    status = "viewable report"
                elif detected == "Video":
                    status = "browser video candidate"
                elif detected == "ZIP":
                    if len(data) > MAX_NESTED_BYTES:
                        raise ValueError("nested ZIP exceeds safety limit")
                    with zipfile.ZipFile(BytesIO(data)) as inner:
                        nested_members = len(inner.namelist())
                    status = "nested ZIP inventoried; contents not decoded"
                    stored_data = None
                elif detected == "XML candidate":
                    xml_elements = _xml_element_names(data)
                    status = "XML parsed: tag names only (values not clinically interpreted)" if xml_elements else "XML-like text; parsing unavailable"
                yield ArchiveMember(
                    name=str(posix), data=stored_data, size_bytes=info.file_size,
                    compressed_bytes=info.compress_size, format=detected,
                    role=role, status=status, sha256=digest,
                    nested_members=nested_members, xml_elements=xml_elements,
                )
            except Exception as exc:
                yield ArchiveMember(
                    name=str(posix), data=None, size_bytes=info.file_size,
                    compressed_bytes=info.compress_size, format="Unknown binary",
                    role="Undecoded / proprietary", status="unreadable/skipped",
                    error=f"{type(exc).__name__}: {str(exc)[:120]}",
                )
