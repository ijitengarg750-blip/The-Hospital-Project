# Per-machine collector credential cutover

This is a controlled maintenance cutover. The backend intentionally has no legacy shared-key fallback.

1. Provision the dashboard's trusted HTTPS endpoint and certificate chain. From every diagnostic PC, verify the HTTPS `/api/health` URL without bypassing certificate validation. Do not begin the credential cutover while only LAN HTTP is available.
2. Back up PostgreSQL and every collector configuration. Confirm source export folders retain files until upload succeeds.
3. Stop only the Data Collector on each diagnostic PC. Do not stop the Remote Connector or TightVNC.
4. Deploy the backend change. Startup adds the `machine_collector_credentials` table without modifying patient, study, Practo UID, report, or remote-session tables.
5. For each active machine, call `POST /api/machines/{machine_id}/collector-credentials` as an administrator. Store the returned key only in that machine's protected local collector configuration; the response is not recoverable later.
6. Replace the collector binary/config field `api_key` with the new binary/config field `collector_key`, then start that machine's collector.
7. Verify one retained or synthetic report uploads over HTTPS, appears under the expected Practo UID, and remains visible to the doctor workflow before moving to the next machine.
8. Confirm the former shared key receives HTTP 401 for every machine. Keep it revoked.

A 401/403 is retried with bounded backoff, so a report encountered during credential rotation is not permanently suppressed. Updating the configuration still requires restarting the collector in this pre-packaging phase.

Rollback must not restore any shared-key backend. Keep ingestion stopped while rolling the collector binary/configuration forward, or roll back only to a backend revision that preserves per-machine credential verification. Source exports remain the recovery queue until collection safely resumes.
