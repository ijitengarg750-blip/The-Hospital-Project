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

## Still unverified

- Real MSI clean install, upgrade, repair, rollback, and uninstall behavior.
- Reboot recovery and independent component recovery on a Windows 10/11 x64 pilot PC.
- Hospital WDAC/App Control admission and production signing.
- Production TLS, TightVNC authentication, and end-to-end clinical workflow behavior.
- Physical browser wizard interaction and accessibility.

These remain later Phase 3D slices or Phase 4 gates and must not be inferred from this automated test.
