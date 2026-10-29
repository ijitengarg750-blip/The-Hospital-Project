# Phase 2 outbound remote-access plan

## Objective

Replace diagnostic-PC inbound remote access with authenticated outbound WSS control and per-session data connections, while retaining Phase 1 only as an explicitly selected lab comparison.

## Architecture decisions

- `REMOTE_ACCESS_MODE` is fail-closed: `disabled`, `inbound_lab`, or `outbound`. There is no automatic fallback between modes.
- Existing per-machine enrollment credentials authenticate both outbound WebSocket channels. Session-scoped, expiring, one-use tickets additionally bind data streams to one authorized session.
- The server owns durable session/audit state; live WebSocket connections are process-local and are reconciled fail-closed after restart.
- TightVNC remains loopback-only. Collection, patient data, reports, and the doctor UI contract remain independent.

## Slices

1. Outbound control registry, authenticated WSS endpoint, heartbeat expiry, and session dispatch.
2. Separate session-authorized outbound data stream and browser relay.
3. Windows connector control/data client with bounded reconnect and independent supervision.
4. Restart/stale-session reconciliation, certificate-rotation behavior, operations configuration, and adversarial review.

## Safety boundaries

- No MSI generation, production deployment, service installation, firewall changes, or host security changes.
- No production plaintext or inbound fallback.
- Phase 1 remains available only through explicit `inbound_lab` configuration.
- Physical/real-Windows gates remain pending until actually performed.
