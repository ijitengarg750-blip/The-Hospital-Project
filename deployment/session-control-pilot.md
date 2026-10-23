# Session-control pilot

This is the first safe remote-access slice. It validates the control plane with a
simulated machine agent; it does not yet authenticate to VNC or change Windows.

## API contract

| Caller | Method and path | Purpose |
| --- | --- | --- |
| Doctor | `POST /api/machines/{machine_id}/agent-credentials` | Create or rotate a machine credential; the plaintext key is returned once. |
| Agent | `POST /api/agent/heartbeat` | Report `unlocked`, `locked`, `signed_out`, `booting`, `busy`, or `unavailable`. |
| Doctor | `POST /api/studies/{study_id}/remote-sessions` | Create a five-minute session and one-time connection grant. |
| Agent | `GET /api/agent/remote-sessions/next` | Poll for the machine's active session. |
| Agent | `POST /api/agent/remote-sessions/{id}/ready` | Confirm the machine is still safe to connect. |
| Doctor | `POST /api/remote-sessions/{id}/connect` | Consume the one-time grant. This will become the gateway handshake. |
| Doctor | `GET /api/remote-sessions/{id}` | Read current state. |
| Doctor | `DELETE /api/remote-sessions/{id}` | End and revoke the session. |
| Agent | `POST /api/agent/remote-sessions/{id}/end` | Local operator termination; old grants remain unusable. |

Agent requests use `X-Machine-Code` and `X-Agent-Key`. Doctor requests use the
existing dashboard bearer token. Run the deployed system over HTTPS or an
approved hospital VPN; headers are not protected on plain HTTP.

## Run the simulator

1. Sign in as the doctor and provision the registered machine through the first
   endpoint above. Store the returned key securely; provisioning it again rotates
   the key immediately.
2. In PowerShell, set the key only for the simulator process and start it:

   ```powershell
   $env:HOSPITAL_AGENT_KEY = '<provisioned key>'
   python deployment/simulated-machine-agent.py --machine-code SIRIUS-TEST-01 --state locked
   ```

3. Create a remote session for a study. The simulator will automatically mark it
   ready without requesting local approval.
4. Consume the returned connection grant once through the doctor connect
   endpoint. A second attempt must return HTTP 409.
5. To exercise local termination from another terminal:

   ```powershell
   $env:HOSPITAL_AGENT_KEY = '<provisioned key>'
   python deployment/simulated-machine-agent.py --machine-code SIRIUS-TEST-01 --end-session '<session id>'
   ```

Clear the environment variable after testing. Do not place a real agent key in a
script, command-line argument, repository file, screenshot, ticket, or log.

## Deliberate limits

- The existing **Open Live Software** button still uses the legacy audited noVNC
  link. It is not connected to this control plane until the trusted gateway can
  exchange the one-time grant for VNC access.
- There is no VNC password storage, gateway authentication, embedded viewer,
  messaging UI, Windows service, DPAPI storage, or Credential Provider.
- Browser-close detection, reconnect leases, backend-restart recovery, and
  offline local enforcement require the gateway and real Windows service.
- The simulator's local termination still needs the backend. A production
  service must also close the local VNC connection when the backend is down.

