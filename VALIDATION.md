# Validation record — Hospital Diagnostic Dashboard

**Updated:** 22 October 2026. Distinguish historical results, tests executed on the user's Windows PC, and tests still pending. A passed unit test is not proof of a hospital-ready deployment.

## Tests actually executed on 22 October 2026

| Area | Command / evidence | Result | Scope |
|---|---|---|---|
| Backend pytest | Disposable Docker container; `python -m pytest -v tests/` | **17 passed, 6 warnings** | SQLite-backed automated API, ingestion and DICOM tests; not live PostgreSQL integration |
| Go collector | `go test ./... -v` | **4 test functions passed** (including HTTP-error subtests) | Retry/failure-state logic |
| Go race detector | `go test -race ./... -v` | **Passed** | No race detected in tested execution paths; not a concurrency guarantee |
| Frontend packages | `npm ci` | **Succeeded; audit reported 0 vulnerabilities** | Local installed dependency set at test time |
| Frontend build | `npm run build` | **Succeeded** (1,932 modules transformed) | TypeScript/Vite compile, not browser E2E |
| Live API health | `curl.exe -i http://localhost:8080/api/health` | **HTTP 200** after Nginx recreation | Existing local deployment, before new Codex changes were redeployed |
| Local collector uploads | Collector console, 22 October | **Multiple `.dcm` uploads logged as successful** | Existing running deployment; not a full comparison after Codex code changes |
| Machine registration | `python setup-local.py --register` | **SIRIUS-TEST-01 already registered** | Existing local PostgreSQL |
| UI | Doctor dashboard screenshot | **Patient record / study / 29 images visible** | Basic UI smoke check, not clinical-content validation |

### Backend pytest cases covered

- Missing/invalid collector keys; valid-key request validation; streaming size limit.
- Bearer-token protection; invalid doctor credentials; registration authorization/duplicate registration; unknown machine rejection.
- DICOM identity and embedded ZIP parsing; duplicate contents under different filenames; distinct studies for one patient.
- Conflicting patient for same Study UID; inactive machine rejection; ingestion cleanup on failure.
- SHA-256 preservation; ZIP traversal and high-compression-ratio protection; missing PatientID rejection.

The six pytest warnings were FastAPI/anyio deprecation notices and pytest cache-write warnings because `/app` was mounted read-only. No test failed. Frontend emitted two third-party `"use client"` bundle warnings; the build succeeded. These should be tracked, not misreported as test failures.

## Earlier validation (21 October 2026; distinct from latest test suite)

Previous project validation reported: synthetic full API path (doctor login, machine registration, authenticated DICOM upload, UID extraction, metadata/report ingestion, authenticated patient lookup) **passed**, with one synthetic study for UID `10042`. The earlier **71-file Sirius comparison** reported 9 patients, 19 studies, 71 DICOM objects, 1,944 embedded ZIP entries, 1,567 JPEG entries, 51 PNG entries, 14 PixelData objects, and zero import failures or compared study-count mismatches. Keep these as **historical validation results**, not claims that the modified Codex build was revalidated end-to-end.

Example `013ed010-88a9-4982-8846-fc597fc243cb.dcm`: 35 embedded entries, 28 JPEG, 1 PNG, six non-image items, and 29 viewable image entries.

## Failure discovered and resolved on local Docker

1. After local setup, backend restarted with PostgreSQL `FATAL: password authentication failed for user "hospital_app"`. An existing PostgreSQL volume retained its initialized password; updating `.env` alone did not rotate that database password.
2. After database authentication was corrected, the frontend still served **502 Bad Gateway** for `/api/health`, login, collector upload and registration. The frontend/Nginx container was older than the newly created backend. `docker compose up -d --force-recreate frontend` restored upstream connectivity; `/api/health` then returned **HTTP 200**.
3. Collector uploads and machine registration succeeded afterward.

**Prevent recurrence:** Do not rotate credentials implicitly, do not delete volumes to fix mismatches, and retest `/api/health` after container replacement. Recreating the frontend is a troubleshooting remedy for a stale upstream, not a universal cure for 502 errors.

## Not yet demonstrated / release gates

**P0 — Before any hospital pilot**

- Isolated **full end-to-end regression of the modified Codex backend and collector**, using distinct Compose project/DB/storage/state and credentials. Prove upload → PostgreSQL → durable source asset → extracted assets → authenticated browser view.
- Real copied-file deduplication and unique examination preservation at the DB and storage levels, not just an `uploaded` log.
- Offline/restart/retry, interrupted transfer, file-written-in-progress, and data integrity after failure **in a running integration environment**.
- Patient identity reconciliation: verify machine PatientID actually equals Practo UID for **each** machine; reject/flag conflicts rather than silently merging people.
- Compare examinations with manufacturer software: images, maps, reports, series/laterality, dates and unknown/proprietary entries; validate no clinically relevant information is silently omitted.
- Backup **and restore** of PostgreSQL plus `clinical-data`, then verify links/hashes in restored instance.
- Per-user authorization/security review, TLS and network restrictions, protected collector key/remote credentials, audit validation, and secure remote-access design.

**P1 — Operational acceptance**

- Windows startup task/service, least-privilege user and collector recovery after reboot.
- Network interruption / server reboot tests on a dedicated non-clinical network.
- Storage capacity, disk-full behavior, monitoring/backlog alerts, large and concurrent uploads.
- Windows versions/CPU architectures found on diagnostic machines; compatibility must be tested, not inferred from Go source.
- Remote noVNC workflow integration per machine (earlier one-machine proof-of-concept does not mean all links work).
- Video/browser compatibility, improved study asset grouping and classification.

**Deferred:** Vendor-specific validated numeric decoding and perfect reconstruction of proprietary software screens.

## Evidence discipline

Use these statuses in future reports: **Passed (executed)**, **Historical pass (earlier version)**, **Written but not executed**, **Blocked by environment**, **Not tested**, **Failed**. Preserve the exact command, date, environment and output with each new result. Never describe unit-test success as production/hospital validation.
