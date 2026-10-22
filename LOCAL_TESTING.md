# Local Windows testing — Docker Desktop + Windows collector

**Updated: 22 October 2026.** Run from `D:\hospital-diagnostic-dashboard` unless otherwise stated. These instructions preserve your working PostgreSQL volume, `.env`, collector credentials and `collector/state.json`.

## First-time local configuration (only on a NEW installation)

```powershell
cd D:\hospital-diagnostic-dashboard
python setup-local.py --init
```

`--init` creates missing `.env` and `collector/config.json` and **does not overwrite existing files**. Do not assume new `.env` credentials update an old initialized PostgreSQL volume; use a planned password migration if needed. Do not run `docker compose down -v`.

For an **existing** installation, use read-only validation instead:

```powershell
python setup-local.py
```

Current local collector parameters: `SIRIUS-TEST-01`, `http://localhost:8080`, watch folder `E:\test\Phoenix_DICOM_Test`, authenticated with `X-Collector-Key`. The DICOM field `(0010,0020) PatientID` is currently used as Practo UID **for this importer**; verify with each actual machine before hospital use. Never publish test files with personal health data.

## Start and inspect the existing deployment

```powershell
docker compose up -d
docker compose ps
curl.exe -i http://localhost:8080/api/health
python setup-local.py --register
```

Expected health: HTTP 200 with `{"status":"ok",...}`. Registration is idempotent for the existing machine. Unlike the previous version of `setup-local.py`, `--register` **does not rewrite `.env` or collector settings**.

If Nginx serves the frontend but API requests give **502**, inspect both sets of logs:

```powershell
docker compose logs backend --tail=100
docker compose logs frontend --tail=100
```

A former issue was a backend/Postgres password mismatch due to an existing volume; another was a stale Nginx upstream after backend recreation. **Only if backend is up and stale Nginx routing is suspected:** `docker compose up -d --force-recreate frontend`. Never delete DB volumes to troubleshoot credentials.

Start the collector in a separate Windows terminal:

```powershell
cd D:\hospital-diagnostic-dashboard\collector
.\hospital-collector.exe .\config.json
```

Successful file transfer logs `uploaded`. That proves server acceptance, but does **not** independently prove complete DICOM extraction or clinical identity matching. Preserve `state.json` to avoid reuploads.

## Automated checks already executed on 22 October

### Backend — **17/17 passed**

From the project root:

```powershell
cd D:\hospital-diagnostic-dashboard
docker compose run --rm --no-deps `
  -v "${PWD}\backend:/app:ro" `
  -w /app `
  -e DATABASE_URL=sqlite:////tmp/test.db `
  -e STORAGE_ROOT=/tmp/test-clinical-data `
  backend sh -c "python -m pytest -v -p no:cacheprovider tests/"
```

Tests previously passed against isolated SQLite and temporary file storage. `-p no:cacheprovider` avoids cache warnings on read-only `/app`. This does **not** establish PostgreSQL-based end-to-end performance. Avoid using `docker compose exec backend pytest` against the live container; use disposable tests instead. Confirm test fixture isolation before rerunning after future changes.

### Collector — **4/4 passed**, race run passed

```powershell
cd D:\hospital-diagnostic-dashboard\collector
go test ./... -v
go test -race ./... -v
```

Race detector success covers only the exercised paths. The tested Go source may be newer than `hospital-collector.exe`; do not assume the executable was rebuilt after Codex edits.

### Frontend — **build passed**

```powershell
cd D:\hospital-diagnostic-dashboard\frontend
npm ci
npm run build
```

The successful build reported 1,932 modules transformed and npm audit 0 vulnerabilities, with non-fatal library bundler warnings. A build is not an interactive browser E2E test.

## Next: isolated integration work (NOT YET EXECUTED)

Have Codex create an independent Compose project with a separate database volume, storage, port, collector state, and test credentials. Never point tests at the existing hospital_dashboard database or `clinical-data` volume. Use copied/de-identified DICOMs, not destructive tests on the watched source folder. Then verify:

1. Upload a DICOM; verify UID, study, series, asset counts and SHA-256 in database and filesystem.
2. Upload an identical copy under a second filename; verify no duplicate clinical assets.
3. Upload different examinations under one patient; verify both remain.
4. Stop test backend/network and restore it; verify retry and no data loss.
5. Simulate an incomplete file; ensure it is not finalized prematurely.
6. Log in to the test dashboard and open every report/image/original asset.
7. Reboot/restoration and backup tests, only in an isolated environment.

Hospital hardware tests and clinical comparison remain separate; see `VALIDATION.md`.

## Security reminders

Keep `.env`, `collector/config.json`, `collector/state.json`, diagnostic files, clinical-data and database backups private. For hospital installation replace all development credentials with hospital-managed secrets using a controlled process, enable encrypted transport and restrict network access; local HTTP should not be used as a general hospital deployment configuration.
