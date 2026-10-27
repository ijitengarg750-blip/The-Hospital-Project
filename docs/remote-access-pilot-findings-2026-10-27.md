# Windows remote-access pilot findings — 27 October 2026

This is a supervised, non-production test record for the current inbound TCP 5910 pilot. It uses synthetic data and a test-only VNC account. It is not approval for hospital deployment.

## Test scope

- Dashboard PC: `192.168.1.68`, Docker dashboard at `https://192.168.1.68:8443`.
- Friend/diagnostic PC: `192.168.1.103`, Windows 11, registered as `FRIEND-TEST-01` / **Friend PC**.
- Transport: browser/noVNC → dashboard gateway → mutually authenticated TLS on TCP 5910 → agent → TightVNC on `127.0.0.1:5900`.

Do not add a LAN or public firewall rule for port 5900. Do not record enrollment tokens, VNC passwords, agent keys, certificates, or private keys in this document.

## Confirmed by the live pilot

- The Windows agent was installed and its service was reachable. The dashboard observed heartbeats from `FRIEND-TEST-01` and reported it online/unlocked during the latest inspection.
- The gateway completed the mutual-TLS connection to the agent and the browser opened the friend PC desktop through the dashboard.
- The browser was not prompted for the TightVNC password. This is expected: the agent authenticates to loopback VNC and the reusable VNC password is not supplied to browser code.
- A doctor message was accepted and stored by the backend for the active remote session. This proves the doctor-to-backend message endpoint worked for that session.
- The synthetic patient `REMOTE-ACCESS-TEST-PATIENT` exists with a study for `FRIEND-TEST-01`. Doctor access must be granted to `FRIEND-TEST-01`; otherwise patient lookup intentionally responds with `No patient found with this Practo UID`.

## Confirmed failure mode before the latest fix

After a remote-PC reboot, a session could remain active and block the next attempt with:

```text
Another remote session is active for this machine
```

The recorded sequence was:

1. The browser created a session.
2. The browser stopped waiting after one minute.
3. The restarting agent marked that session ready later.
4. The browser no longer had a usable connection grant, but the server kept the session active until its five-minute expiry.
5. New attempts were rejected while that active slot remained occupied.

There was a second related server-side gap: when an established agent tunnel closed normally (for example, while Windows rebooted), the gateway did not clear the connected session's active slot.

## Changes implemented and deployed

Commit `5b9a821` (`fix: release remote sessions after disconnects`) was deployed to the dashboard backend and frontend on 27 October 2026.

- The gateway now marks a connected session failed, records `ended_at`, and releases the active slot when its viewer or agent tunnel disconnects. A doctor-side explicit end still wins and is not overwritten.
- The frontend now deletes a session if the agent does not become ready or viewer-ticket creation fails, so retrying does not require waiting for the five-minute server expiry.
- A regression test proves that a closed encrypted gateway connection releases the active slot.
- The agent installer now creates a startup shortcut that runs the signed notification UI with PowerShell `AllSigned` policy and starts it immediately when the installer runs as the configured UI user.
- A fresh Authenticode-signed pilot package was generated at `release/HospitalAgentPilot` using the existing pilot signing certificate. The old friend-PC installation is not automatically changed by rebuilding this package.

Verification completed for this change:

- Backend: 52 pytest cases passed.
- Frontend: TypeScript/Vite production build passed.
- Windows agent: `go test ./...` passed.
- Installer: Pester tests passed.
- Dashboard backend/frontend containers were rebuilt and restarted; the agent resumed successful heartbeat/session polling afterward.

## Not yet confirmed

- The reboot/disconnect fix has **not** been manually retested on the two physical PCs after deployment. It is covered by automated regression testing only.
- The local friend-PC notification UI did not appear during the observed session. The backend stored the doctor message, but the UI process lookup returned no process. The likely cause is that the original shortcut launched PowerShell without an explicit signed-script execution-policy argument. This is an inference, not a confirmed root cause.
- The updated shortcut/notification UI has **not** been manually verified on the existing friend-PC installation. That installation needs either the documented one-time shortcut repair or reinstallation from the new signed package.
- End-to-end local-operator replies, local **End session**, and message display have not yet been reconfirmed after the UI repair.
- A dashboard-specific **Restart remote PC** feature does not exist. Windows can only be restarted through the existing remote desktop if the signed-in Windows account has permission; that is not a managed restart workflow and was not added by this pilot.
- WDAC/App Control compatibility is still unverified. Valid self-signed pilot signatures and a temporarily disabled Smart App Control setting do not demonstrate hospital-policy approval.
- This remains an inbound TCP 5910 pilot with manual installation. The outbound WSS redesign is not implemented.

## Next supervised test

1. Hard-refresh the dashboard browser to load the deployed frontend.
2. On the friend PC, repair or reinstall the signed notification UI launcher, then verify `HospitalRemoteUI.ps1` is running for the configured Windows user.
3. Open the synthetic `FRIEND-TEST-01` study and confirm the local notification window displays the doctor message.
4. End one session normally from each side.
5. During a separate session, restart the friend PC through its Windows desktop. After the friend signs in and the agent reports online, create a new session immediately. It must not report `Another remote session is active for this machine`.
6. Record the result, timestamps, session status, and any safe error text. Never record credentials or patient data.

## Current operational state at last inspection

At the last dashboard database inspection after deployment, `FRIEND-TEST-01` was heartbeating as `unlocked` and had no active remote-session slot. This is an observation from that moment, not a guarantee of its current state.
