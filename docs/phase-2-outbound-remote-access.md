# Phase 2 outbound remote access

Status: development implementation; not approved for production deployment.

## Transport

Set `REMOTE_ACCESS_MODE=outbound` on the central server. The enrolled Windows Remote Connector derives `wss://<stable-dashboard-host>/api/agent/control` from its HTTPS dashboard URL, authenticates with its DPAPI-protected machine credential, and maintains heartbeats with bounded reconnect backoff. DHCP changes on the diagnostic PC do not require server updates because the PC is always the initiator.

For each authorized doctor request, the server sends a session ID plus a short-lived signed data ticket over that control connection. After authenticating local TightVNC on loopback, the connector opens a second outbound WSS connection. The ticket is sent in a header, consumed once, scoped to one machine/session, and never placed in a URL. The browser's existing one-use viewer ticket remains separate.

The `inbound_lab` mode retains Phase 1 for comparison testing. Modes never fall back to each other. `disabled` is the server default. Outbound installation does not start the TCP 5910 listener; production must not retain an inbound 5910 firewall rule.

## Certificates

The dashboard URL must be HTTPS except for loopback development. The connector uses the Windows trusted-root store and validates the configured stable hostname with TLS 1.2 or newer. Rotate server certificates using normal overlap: deploy a chain trusted by diagnostic PCs before switching the leaf, keep the hostname unchanged, verify reconnects, then revoke/remove the old chain. Do not add certificate-bypass or plaintext fallback code.

## Recovery and reconciliation

- Control disconnect closes that machine's data streams and fails active sessions; the connector reconnects with backoff.
- Server startup fails any surviving active session with `server_restart`; it never silently reauthorizes an old stream.
- Expired sessions are cleaned every 15 seconds and audited.
- Offline local End Session closes the local stream first and stores its retry intent in a separate DPAPI-protected, service-only state file until the server accepts the end.
- Remote Connector, notification UI, local VNC readiness, and Data Collector remain separate processes/components. The connector supervisor restarts only the failed connector loop.

## Deployment topology invariant

The live control/data registry and replay ledger are process-local by design in this first implementation. Run exactly one backend process/replica (the checked-in Dockerfile does this) and do not add Uvicorn workers or horizontal replicas. Scaling requires a shared connection router and shared atomic replay store before rollout; sticky routing alone is not sufficient for replay protection.

## Development validation limits

Automated tests use loopback HTTP/WS only where explicitly allowed and synthetic VNC. They do not prove Windows service behavior, hospital PKI trust, WDAC acceptance, Wi-Fi/DHCP recovery, or two-laptop desktop operation. Those gates remain open in `tasks/todo.md` and `deployment/visit-checklist.md`.
