from datetime import datetime
from pathlib import Path
import zipfile
from io import BytesIO

from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from app.dicom_import import embedded_archives, inspect_dicom, safe_archive_members


def make_dicom(path: Path):
    meta = Dataset()
    meta.MediaStorageSOPClassUID = generate_uid()
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientID = "10042"
    ds.PatientName = "Ramesh^Sharma"
    ds.PatientBirthDate = "19650312"
    ds.StudyInstanceUID = generate_uid()
    ds.StudyDescription = "Pupillography"
    ds.StudyDate = "20261021"
    ds.StudyTime = "103200"
    ds.Modality = "OP"
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ExamInfo.xml", "<ExamInfo />")
        archive.writestr("Reports/report.jpg", b"\xff\xd8\xfftest")
    ds.add_new((0x7001, 0x1098), "OB", buffer.getvalue())
    ds.save_as(path, enforce_file_format=True)


def test_reads_identity_and_embedded_zip(tmp_path):
    path = tmp_path / "sample.dcm"
    make_dicom(path)
    ds, info = inspect_dicom(path)
    assert info.patient_uid == "10042"
    assert info.patient_name == "Ramesh Sharma"
    assert info.acquisition_datetime == datetime.fromisoformat("2026-10-21T10:32:00+00:00")
    archives = embedded_archives(ds)
    assert len(archives) == 1
    names = [member.name for member in safe_archive_members(archives[0].data)]
    assert names == ["ExamInfo.xml", "Reports/report.jpg"]

