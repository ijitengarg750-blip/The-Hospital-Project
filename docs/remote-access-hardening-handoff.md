# Remote-access hardening handoff

Date: 2026-10-25
Branch: `feature/remote-access-hardening`

For the later live friend-PC pilot, including confirmed findings, unconfirmed assumptions, and the post-pilot disconnect fix, see [remote-access-pilot-findings-2026-10-27.md](remote-access-pilot-findings-2026-10-27.md).

## Delivered

- Admin and doctor roles are embedded in short-lived signed sessions. Browser authentication uses an HttpOnly, Secure, SameSite=Strict cookie; no token is stored in browser storage.
- Login failures are throttled by hashed client-address/username key (default 5 attempts per 15 minutes). `MFA_REQUIRED` is an integration/readiness flag only; an MFA provider and challenge flow are not implemented.
- Machine registration, network updates, four separate operational states, encrypted connection testing, one-time enrollment, credential rotation, doctor grant/revoke, machine revocation, and audit viewing are admin-only.
- Doctor permissions are enforced server-side for patient history, files, reports, inventories, and remote sessions. Patient history omits studies on ungranted machines and returns not-found when none are authorized.
- Enrollment tokens are random, stored only as SHA-256 hashes, expire after 10 minutes, and are one-use. The browser receives only the enrollment token; the agent credential is returned only to the enrolling agent.
- Gateway-to-agent VNC traffic requires TLS 1.2+ with certificates verified in both directions. The agent authenticates to VNC on loopback and stores its API key, private key, gateway CA, and VNC password inside the existing DPAPI-protected service configuration.
- The WebSocket gateway has no plaintext fallback. Direct password-file VNC helpers remain solely for legacy/test compatibility and are not called by the production remote-session route.
- Modern amd64 plus Go 1.20 legacy amd64/386 build targets remain available. RFB 3.3, 3.7, and 3.8 authentication negotiation is supported behind the encrypted tunnel.

## Operator configuration

Backend:

```text
VNC_GATEWAY_CA_CERT_FILE=/run/mtls/gateway-ca.crt
VNC_GATEWAY_CLIENT_CERT_FILE=/run/mtls/gateway-client.crt
VNC_GATEWAY_CLIENT_KEY_FILE=/run/mtls/gateway-client.key
AUTH_MAX_ATTEMPTS=5
AUTH_WINDOW_MINUTES=15
SESSION_COOKIE_SECURE=true
```

Generate only disposable lab identities with `deployment/New-TestTlsCertificate.ps1`. Production certificates must come from the hospital-approved identity process and the private key file must be readable only by the backend runtime identity.

On each diagnostic PC, enroll from an elevated console with the one-time token, keep VNC on `127.0.0.1:5900`, and allow agent port 5910 only from the gateway address. No VPN or broad firewall rule is installed by this project.

## Verification completed

- Backend: 52 pytest cases, including authorization filtering, one-time enrollment/rotation/revocation, login throttling, secure-gateway failure, and remote-session behavior.
- Agent: current Go and Go 1.20.14 suites; real mTLS proxy test; plaintext rejection; legacy RFB 3.3 negotiation.
- Builds: React/Vite production build; modern amd64, legacy amd64, and legacy 386 agent artifacts; Docker Compose merged configuration.
- Packaging: PowerShell parser validation for certificate preparation and provisioning scripts.
- Dependency scan: `npm audit --omit=dev --audit-level=high` reported zero vulnerabilities.
- Review mutation: inverting the doctor authorization branch made the grant-filter test fail, confirming that the regression test exercises the boundary.

## Still requires supervised environment validation

- Real two-laptop encrypted desktop run using the exact hospital VNC product/version.
- Windows 7 SP1, 8, and 8.1 execution on each claimed architecture; cross-build success is not runtime certification.
- Certificate issuance/rotation/revocation procedure, backup/restore, gateway address changes, and firewall review by hospital IT.
- Locked, signed-out, reboot, network outage, concurrent-session, and local emergency-stop behavior.
- Production MFA provider integration. Setting `MFA_REQUIRED=true` alone does not add a second factor.

## Rollback

Stop use of remote sessions, revoke each machine from **Diagnostic machines**, uninstall the agent with `Uninstall-Agent.ps1 -RemoveConfiguration`, remove the named 5910 firewall rule, and stop the remote-access Compose overlay. Leave clinical collection and stored studies intact. Do not delete database volumes or `clinical-data` as part of remote-access rollback.
