# Phase 3A: guided diagnostic-machine enrollment

Date: 2026-10-29

## Implemented

- The existing administrator-only Diagnostic Machines page now guides an administrator through registration or selection, invitation creation, installer approval, component validation, and readiness confirmation.
- Registration follows the Phase 2 outbound HTTPS/WSS design and no longer requires a diagnostic-machine IP address. Legacy inbound host and TLS fields remain in expandable administrator diagnostics.
- Enrollment invitations last ten minutes, invalidate earlier unused invitations, permit at most five claims, require a matching six-digit code, and never return an enrollment credential to the browser.
- The Windows agent reports its existing TightVNC and Local Notification UI checks with its heartbeat. Raw local error strings are not stored or returned by the machine-status API.
- Machine status reports Remote Connector, TightVNC, Data Collector, Local Notification UI, and Remote Session separately. Remote Connector connectivity is not treated as remote-session readiness.
- Data Collector status is based on credential presence and the latest recorded collection result. It does not infer a live collector process from agent connectivity.
- Network details, connection errors, doctor grants, disable controls, and audit events are under expandable administrator diagnostics.

## Automated verification recorded for this slice

- Frontend TypeScript and production build: passed.
- Full backend suite in a disposable Python 3.12 environment with isolated SQLite databases: 71 passed.
- Windows agent `go test ./...` with fresh temporary caches: passed.
- Installer Pester suite: 7 passed.
- Changed Python files: syntax compilation passed.

Docker Desktop was not running, so the backend suite used a disposable Python environment rather than a container. No running hospital container, PostgreSQL volume, clinical record, installed Windows service, firewall rule, or Windows security policy was changed.

## Still unverified

- Browser interaction, keyboard flow, and responsive rendering have not been exercised against a running backend.
- A signed installer has not been installed on a Windows 10/11 x64 pilot PC with WDAC enabled.
- TightVNC, Local Notification UI, Data Collector, service recovery, and remote-session readiness still require physical two-PC testing.
- Final signing, production MSI packaging, approved WDAC execution, production deployment, and Windows 7/8/8.1 compatibility remain Phase 4 gates.

## Phase 3 boundaries preserved

This slice does not change Practo UID mapping, DICOM extraction, reports, clinical storage, the Phase 2 outbound HTTPS/WSS topology, the one-backend-process deployment constraint, or the prohibition on insecure inbound fallback.
