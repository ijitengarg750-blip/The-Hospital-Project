"""Restore a live DB snapshot and copied files into disposable local storage."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml")]
TARGET = ["docker", "compose", "-p", "hospital-restore-verification", "-f", str(ROOT / "deployment/compose.restore-check.yml")]


def run(command, data=None):
    return subprocess.run(command, cwd=ROOT, input=data, stdout=subprocess.PIPE, check=True).stdout


def hashes(folder):
    return {str(path.relative_to(folder)): sha256(path.read_bytes()).hexdigest() for path in folder.rglob("*") if path.is_file()}


def verify():
    files = ROOT / "clinical-data"
    before = hashes(files)
    # Capture bytes directly: shell redirection on Windows may change SQL encoding.
    dump = run(SOURCE + ["exec", "-T", "db", "sh", "-c", 'pg_dump --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"'])
    started = False
    try:
        run(TARGET + ["up", "-d", "--wait"])
        started = True
        with tempfile.TemporaryDirectory(prefix="hospital-restore-verification-") as temporary:
            copied = Path(temporary) / "clinical-data"
            shutil.copytree(files, copied)
            assert before == hashes(files) == hashes(copied), "Clinical files changed during backup; repeat with collection paused"
            run(TARGET + ["exec", "-T", "restore-db", "psql", "-U", "verification", "-d", "verification", "-v", "ON_ERROR_STOP=1"], dump)
            query = "SELECT coalesce(json_agg(row_to_json(f)), '[]'::json) FROM (SELECT relative_path, sha256 FROM clinical_files) f"
            records = json.loads(run(TARGET + ["exec", "-T", "restore-db", "psql", "-U", "verification", "-d", "verification", "-Atc", query]))
            failures = {"source_missing": 0, "source_hash_mismatch": 0, "restored_missing": 0, "restored_hash_mismatch": 0}
            for record in records:
                path = (copied / record["relative_path"]).resolve()
                assert copied.resolve() in path.parents, "Unexpected path outside restored clinical storage"
                original = files / record["relative_path"]
                if not original.is_file(): failures["source_missing"] += 1
                elif sha256(original.read_bytes()).hexdigest() != record["sha256"]: failures["source_hash_mismatch"] += 1
                if not path.is_file(): failures["restored_missing"] += 1
                elif sha256(path.read_bytes()).hexdigest() != record["sha256"]: failures["restored_hash_mismatch"] += 1
            if any(failures.values()):
                raise RuntimeError(f"Backup validation failed across {len(records)} indexed files: {failures}")
            print(f"PASS PostgreSQL dump restored into isolated PostgreSQL 16; all {len(records)} indexed file hashes match copied storage")
            print("Live database and clinical files were read only. No backup archive was retained; this checks restore, not scheduled backup or retention.")
    finally:
        if started:
            run(TARGET + ["down"])


if __name__ == "__main__":
    verify()
