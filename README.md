# Hospital Diagnostic Dashboard

An on-premises system that collects diagnostic exports, indexes them by Practo UID, shows patient history in a browser, and launches each machine's live noVNC session.

This prototype/pilot-oriented build is intentionally **report-first**. It is intended to preserve original DICOM/proprietary files, extracts useful CSO/Sirius content, displays reports/images/videos when available, and relies on remote access for anything that still requires the manufacturer's software.

## What is implemented

- One-doctor authenticated dashboard.
- Exact Practo UID lookup.
- Patient history grouped by examination date.
- Report, image, video and original-DICOM access.
- Machine-specific **Open live software** links.
- PostgreSQL index with clinical files stored in a server folder.
- DICOM parsing for patient identity, study identity and acquisition time.
- Extraction of ZIP archives embedded in CSO private DICOM tags `(7001,1098)` and `(7001,1099)`.
- Preserved hierarchy: patient → study → series → DICOM object → embedded entry.
- Separate counts for DICOM objects, ZIP entries, image entries and unique image content.
- The same role rules as the validated offline `v2.py` investigation: Dynamic, Mesopic, Photopic, Scotopic, `.tmb` preview, `.cso`, XML, nested archive and undecoded/proprietary.
- Configurable Windows folder collector with stable-file checks, SHA-256 deduplication, retries and an offline state file.
- DICOM plus non-DICOM PDF/image/video upload support.
- Audit records for login, patient viewing, file viewing and remote-session launching.
- Docker-based server deployment.

## Architecture

```text
Windows 7–11 diagnostic PC
  PACS export folder -> Collector -> Hospital server

Hospital server
  Nginx/React -> FastAPI -> PostgreSQL
                         -> clinical-data folder

Doctor browser
  Practo UID search -> report/history -> noVNC link
```

## Important identity rule

For the current Sirius importer, `(0010,0020) PatientID` is treated as the Practo UID; this mapping must be validated on each hospital machine. A DICOM with no PatientID is rejected instead of being attached to the wrong patient.

The five-digit value is the content of that standard DICOM field; `(0010,0020)` is only the field address. Random UUID-like filenames are not used for patient matching.

For a non-DICOM PDF/image/video machine, configure `practo_uid_regex` in the collector so the UID is extracted from its folder or filename. If several files belong to one examination, also configure `study_uid_regex` so they are grouped together.

## Local PC smoke test

For Docker Desktop on your Windows PC, follow **[LOCAL_TESTING.md](LOCAL_TESTING.md)** first. For a NEW setup, run `python setup-local.py --init` to create missing credentials/configuration only. For an existing installation, `python setup-local.py` validates config without modification. After starting Docker, `python setup-local.py --register` registers the machine without changing secrets. See `LOCAL_TESTING.md` for the tested commands and Docker 502 troubleshooting.

## Quick server installation

Recommended server: Ubuntu 22.04/24.04 with a fixed LAN IP, at least 8 GB RAM, and storage sized for the hospital's exports.

1. Install Docker Engine and the Docker Compose plugin.
2. Copy this project to the server.
3. Copy `.env.example` to `.env`.
4. Replace every placeholder secret in `.env`.
5. Start the system:

   ```bash
   docker compose up -d --build
   ```

6. Open `http://SERVER_IP:8080` on the hospital LAN.
7. Confirm `http://SERVER_IP:8080/api/health` returns `status: ok`.

Do not expose port 8080 directly to the public internet. Use it on the hospital LAN or behind the hospital VPN.

## Register the first machine

Sign in to get a token, then create the Sirius machine. Replace the server address, password and noVNC URL:

```bash
TOKEN=$(curl -s http://SERVER_IP:8080/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"doctor","password":"YOUR_PASSWORD"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl http://SERVER_IP:8080/api/machines \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "code":"SIRIUS-ROOM-2",
    "name":"CSO Sirius",
    "modality":"Pupillography",
    "location":"Diagnostic Room 2",
    "no_vnc_url":"http://SERVER_IP:6080/vnc.html?autoconnect=true"
  }'
```

No VNC password is stored in PostgreSQL or included in this project. The doctor enters it in the noVNC page. Restrict VNC to the hospital LAN and use a different strong password per machine.

## Build the Windows collector

The collector uses the Go standard library and is designed to be built with **Go 1.20**, the last Go release line supporting Windows 7/8. Build both architectures on a development computer:

```powershell
$env:GOOS="windows"
$env:GOARCH="amd64"
go build -trimpath -ldflags="-s -w" -o hospital-collector-x64.exe .

$env:GOARCH="386"
go build -trimpath -ldflags="-s -w" -o hospital-collector-x86.exe .
```

Use x64 for 64-bit Windows and x86 for 32-bit Windows. The resulting executable requires no Go installation on the diagnostic PC.

## Configure a diagnostic PC

1. Create `C:\HospitalCollector`.
2. Copy the correct collector executable to it as `hospital-collector.exe`.
3. Copy `config.example.json` to `config.json`.
4. Set `machine_code`, server IP, collector API key and actual PACS export folder.
5. Run it manually once in Command Prompt:

   ```cmd
   cd C:\HospitalCollector
   hospital-collector.exe config.json
   ```

6. Finish one test examination.
7. Confirm the log says `uploaded` and the Practo UID appears in the dashboard.
8. Install it as an automatic startup task using `deployment\install-collector-task.ps1` under a dedicated low-privilege Windows account.

The collector only reads source files. It never deletes or changes PACS exports.

## Machine configuration examples

### DICOM machine

```json
{
  "machine_code": "SIRIUS-ROOM-2",
  "server_url": "http://192.168.1.10:8080",
  "api_key": "same value as COLLECTOR_API_KEY",
  "watch_folders": ["C:\\PACS_EXPORT"],
  "extensions": [".dcm"],
  "practo_uid_regex": "",
  "study_uid_regex": "",
  "study_title": "Pupillography",
  "poll_seconds": 10,
  "stable_checks": 3,
  "state_file": "C:\\HospitalCollector\\state.json"
}
```

### PDF machine whose folders contain the Practo UID

For a path such as `D:\Exports\10042\2026-10-21\report.pdf`:

```json
{
  "machine_code": "OCT-ROOM-1",
  "server_url": "http://192.168.1.10:8080",
  "api_key": "same value as COLLECTOR_API_KEY",
  "watch_folders": ["D:\\Exports"],
  "extensions": [".pdf", ".jpg", ".mp4"],
  "practo_uid_regex": "Exports[\\\\/](\\d+)",
  "study_uid_regex": "(\\d{4}-\\d{2}-\\d{2})",
  "study_title": "OCT examination",
  "poll_seconds": 10,
  "stable_checks": 3,
  "state_file": "C:\\HospitalCollector\\state.json"
}
```

The regex rules must be tested on each machine before real use. DICOM machines do not need them because the UID comes from PatientID.

## Hospital-visit onboarding order

For each machine:

1. Record machine name, room, Windows version and 32/64-bit type.
2. Locate and document the automatic PACS/export folder.
3. Complete one test examination using a known Practo UID.
4. Inspect the export format and confirm the UID source.
5. Register the machine through the API.
6. Configure and run the collector manually.
7. Confirm the new study appears under the correct UID.
8. Open the report from the dashboard.
9. Configure/test noVNC.
10. Install collector auto-start and restart the PC once to verify it.

A machine counts as integrated only after collection, correct UID matching, report viewing and remote access all work end to end.

## Backup

Back up both of these together:

- PostgreSQL database volume
- `clinical-data` directory

The database tells the dashboard which files belong to which patients; the directory contains the actual clinical files. A backup of only one is incomplete.

## Current deliberate limits

- No proprietary numerical decoding is claimed as clinically valid.
- Image classification uses filenames/types and may leave items as general images.
- "Other images / possible report or preview" remains intentionally uncertain until its clinical purpose is verified against Sirius.
- Browser video appears only when an extracted browser-compatible format is available.
- noVNC credentials are entered at the noVNC page rather than stored in the dashboard database.
- Before routine clinical use, the hospital must approve user access, retention, backup, LAN/VPN exposure and audit policy.

## Development checks

Backend:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

Frontend:

```bash
cd frontend
npm install
npm run build
```



## Status and validation — updated 22 October 2026

**Working in the Windows local pilot (existing deployed build):** backend `/api/health` responds HTTP 200; `SIRIUS-TEST-01` is registered; Go collector detected and uploaded multiple `.dcm` files from `E:\test\Phoenix_DICOM_Test`; dashboard displayed a patient history and a study containing 29 viewable images. The one-machine noVNC experiment reported earlier is separate from this collector integration.

**Executed automated checks on updated source:** 17/17 backend pytest cases passed in a disposable SQLite-backed Docker environment; four Go collector test functions passed normally and under `go test -race`; frontend `npm ci` and `npm run build` succeeded (audit reported zero vulnerabilities). Six non-fatal pytest warnings and two non-fatal Vite bundling warnings were observed. See [`VALIDATION.md`](VALIDATION.md) for exact scope.

**Critical unverified milestone:** end-to-end regression of the **modified** Codex backend/collector against a completely isolated PostgreSQL + Docker + browser test stack. The live upload demonstration and the new automated unit/integration tests were not the same end-to-end run.

**Must complete before a supervised hospital pilot:** verify identity mapping and report/image completeness against the manufacturer's software; database/filesystem deduplication with real copies; network outage and restart recovery; backup/restore; secure machine authentication, HTTPS/remote access, permissions, and Windows auto-start under a least-privilege service account. Other diagnostic machines require independent export/UID/compatibility tests.

**Known deployment issue resolved:** an existing PostgreSQL Docker volume retained an older password after config changes, causing backend crashes. After correcting database authentication, an older Nginx frontend container still returned 502 until `docker compose up -d --force-recreate frontend`. New `setup-local.py` no longer rewrites credentials during registration. Do not run `docker compose down -v` against saved records.

### Project milestone order

1. Isolated modified-stack regression and backup/restore.
2. Verify study/series identity and content completeness against Sirius/Phoenix.
3. Validate failure recovery, credential security, permissions, monitoring and Windows startup.
4. Test full doctor workflow with live remote access on one approved hospital machine.
5. Expand machine by machine (new UID/export adapters), then refine clinical asset presentation and optional video/proprietary measurement support.

Current limitations: the image/report classification remains partly generic; not all proprietary data has validated interpretation; browser video and the noVNC workflow vary by machine. The existing doctor-login demonstration is not sufficient evidence of multi-user role authorization or clinical readiness.
