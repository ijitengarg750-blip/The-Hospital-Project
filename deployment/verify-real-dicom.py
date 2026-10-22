"""Read one real export; verify duplicate ingestion in temporary SQLite/storage only."""
import argparse
from hashlib import sha256
from pathlib import Path
import tempfile

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import ingestion
from app.config import Settings
from app.database import Base
from app.models import ClinicalFile, DicomObject, Machine, Study


def verify(source: Path):
    original_hash = sha256(source.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="hospital-real-dicom-") as temporary:
        storage = Path(temporary) / "files"
        settings = Settings(storage_root=storage)
        previous_settings = ingestion.get_settings
        ingestion.get_settings = lambda: settings
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        try:
            with Session(engine, expire_on_commit=False) as db:
                db.add(Machine(code="ISOLATED-TEST", name="Temporary duplicate validation"))
                db.commit()
                study = ingestion.ingest_upload(db, "ISOLATED-TEST", source, "original.dcm")
                first_counts = (len(list(db.scalars(select(DicomObject)))), len(list(db.scalars(select(ClinicalFile)))))
                stored_before = {str(path.relative_to(storage)): sha256(path.read_bytes()).hexdigest() for path in storage.rglob("*") if path.is_file()}
                repeated = ingestion.ingest_upload(db, "ISOLATED-TEST", source, "renamed-copy.dcm")
                final_counts = (len(list(db.scalars(select(DicomObject)))), len(list(db.scalars(select(ClinicalFile)))))
                stored_after = {str(path.relative_to(storage)): sha256(path.read_bytes()).hexdigest() for path in storage.rglob("*") if path.is_file()}
                assert repeated.id == study.id and first_counts == final_counts
                assert stored_before == stored_after
                assert len(list(db.scalars(select(Study)))) == 1
                for item in db.scalars(select(ClinicalFile)):
                    assert sha256((storage / item.relative_path).read_bytes()).hexdigest() == item.sha256
                print(f"PASS real renamed duplicate: {final_counts[0]} DICOM object(s), {final_counts[1]} clinical file(s); counts and all stored hashes unchanged")
                print(f"Report documents: {sum(item.kind == 'report' for item in study.files)}; extracted images: {sum(item.kind == 'image' for item in study.files)}")
                # A stopped copy is a separate check from an actively growing file.
                partial = Path(temporary) / "incomplete.dcm"
                content = source.read_bytes()
                partial.write_bytes(content[:len(content) // 2])
                partial_engine = create_engine("sqlite://")
                Base.metadata.create_all(partial_engine)
                settings.storage_root = Path(temporary) / "partial-files"
                try:
                    with Session(partial_engine) as partial_db:
                        partial_db.add(Machine(code="ISOLATED-TEST", name="Partial-copy check"))
                        partial_db.commit()
                        ingestion.ingest_upload(partial_db, "ISOLATED-TEST", partial, "incomplete.dcm")
                except Exception as error:
                    print(f"Partial-copy ingestion rejected ({type(error).__name__})")
                else:
                    print("PENDING: partial-copy ingestion was accepted; stable size is not proof of complete export")
                finally:
                    partial_engine.dispose()
        finally:
            ingestion.get_settings = previous_settings
            engine.dispose()
    assert sha256(source.read_bytes()).hexdigest() == original_hash
    print("PASS source file unchanged; live database/storage were not used")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    verify(parser.parse_args().source)
