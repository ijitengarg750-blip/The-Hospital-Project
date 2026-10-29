# Phase 3D — testing and verification

## Slice 1: repeated enrollment attempts

This slice adds focused backend verification for the guided enrollment attempt limit and recovery path:

- the first five claims for an invitation are accepted;
- a sixth claim fails with an actionable `429` response;
- issuing a replacement invitation permits a fresh claim; and
- a claim from the superseded invitation cannot be approved or complete enrollment.

The approval query now rejects claims whose parent invitation was superseded instead of returning a false successful approval to the administrator.

The test uses a disposable SQLite database and generated in-memory enrollment keys. It does not start Docker, modify PostgreSQL or clinical records, install an MSI, register a Windows service, or execute an unsigned service.

Static Python compilation and diff checks passed. The focused pytest run passed in the disposable Python 3.12 environment supplied for this verification:

- `1 passed, 7 warnings in 3.71s`;
- the warnings are existing FastAPI/Starlette lifecycle and test-client deprecations, not failures in the enrollment behavior; and
- the environment was isolated under `%TEMP%` and did not use the repository's historical `.venv`.

Environment notes for future sessions:

- `python` and `py` resolve only to `C:\Python315\python.exe` (Python 3.15.1).
- `backend\.venv` is the historical environment name; `backend\venv` does not exist.
- `.venv` still contains launchers and records Python 3.12.10, but its base interpreter at `C:\Users\Lenovo\AppData\Local\Programs\Python\Python312\python.exe` is missing, so the environment cannot run.
- Global Python 3.15 has user-installed pytest 9.2.0 but not the backend libraries, which explains the initial missing `Crypto` import.
- A disposable Python 3.15 environment can install most SQLite-test dependencies, but SQLAlchemy 2.0.36 fails while parsing model annotations on Python 3.15 before pytest can collect tests.
- The pinned `psycopg-binary==3.2.3` also has no Python 3.15 distribution; the repository's verified backend-test target remains Python 3.12.

Do not install packages one at a time into global Python for this suite. Use Python 3.12, create a disposable environment, install `backend\requirements.txt`, and invoke that environment's `python.exe` directly; activation is optional. A virtual environment stops working if its recorded base interpreter is later removed.

## Phase 3D coverage matrix

| Required behavior | Automated evidence | Current result |
| --- | --- | --- |
| Wizard state and enrollment errors | `test_machine_registration_returns_separate_operational_states`, `test_enrollment_claim_waits_for_matching_admin_approval` | Passed in the Phase 3A backend suite |
| Failed and repeated enrollment attempts | `test_repeated_enrollment_attempts_require_a_new_invitation` | Passed: 1 test in Python 3.12 |
| Expired enrollment invitations | `test_expired_enrollment_invitation_is_rejected` | Passed in the Phase 3A backend suite |
| Preflight failures | `Preflight.Tests.ps1` and `Install-Agent.Tests.ps1` | Passed in the current Pester run |
| Component-specific health reporting | `test_component_health_is_reported_independently_and_without_agent_details` and agent component tests | Passed in backend/Go verification |
| Install, upgrade, repair, and rollback policy | `Installer.Tests.ps1`, MSI compilation, and unsuppressed WiX ICE validation | Passed for source/build validation; physical lifecycle is a Phase 4 gate |
| Preserve enrollment credentials and queued reports | focused uninstall assertions, collector credential-rotation retry test, and report-ingestion continuity test | Passed for source/Go checks; current full backend rerun exceeded the local cutoff |
| Independent component recovery | `TestComponentSupervisorRecoversAfterOnlyThatComponentStops` and independent MSI feature/task assertions | Passed in the current Go/Pester runs |
| Correct errors and administrative permissions | machine-admin invitation, health, and doctor-permission tests | Passed in the Phase 3A suite plus the current focused regression |

## Current development verification

- Focused Python 3.12 regression: `1 passed, 7 warnings in 3.71s`.
- Windows agent Go suite: passed for `agent`, `hospital-installer-launcher`, and `hospital-remote-agent`; the Windows authentication proof-of-concept has no tests.
- Collector Go suite: passed, including queued-report retry and credential-rotation coverage.
- Pester preflight, installer, and MSI source suites: passed. These tests are side-effect-free source/logic checks and do not install an MSI or service.
- Frontend TypeScript and Vite production build: passed. Vite emitted existing third-party `use client` directive warnings from `lucide-react`.
- Phase 3C MSI compilation and unsuppressed WiX ICE validation were previously confirmed with exit code `0` for `windows-agent\installer\out\HospitalDiagnosticComponents-0.1.0-x64.msi`.

The current full backend rerun was stopped after exceeding the requested 30-second tool cutoff, so it is `Not Tested` in this final run rather than silently treated as passed. Run it from `backend` with the disposable Python 3.12 interpreter:

```powershell
& "$env:TEMP\hospital-backend-py312\Scripts\python.exe" -m pytest -q `
  --basetemp "$env:TEMP\hospital-phase3d-full" -p no:cacheprovider
```

## Phase 4 gates (not verified by Phase 3D automation)

- Real MSI clean install, upgrade, repair, rollback, and uninstall behavior.
- Reboot recovery and independent component recovery on a Windows 10/11 x64 pilot PC.
- Hospital WDAC/App Control admission and production signing.
- Production TLS, TightVNC authentication, and end-to-end clinical workflow behavior.
- Physical browser wizard interaction and accessibility.

These must not be inferred from automated source, unit, build, or ICE checks. No unsigned Windows service was installed or run, and no running hospital container, PostgreSQL volume, clinical record, firewall rule, or Windows security policy was changed.
