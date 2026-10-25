# One-click remote access pilot

## What is implemented

The dashboard now uses `POST /api/studies/{id}/remote-sessions`, waits for the Windows agent to mark the request ready, exchanges the connection grant for a 30-second one-use viewer ticket, and opens the noVNC 1.7.0 library inside the dashboard. The viewer ticket is carried as a WebSocket subprotocol. It is not a VNC or Windows credential.

The backend gateway performs classic RFB/VNC authentication type 2 itself and then exposes an already-authenticated RFB byte stream to noVNC. The VNC password is read from a Docker secret file. It is not returned by an API, put in a URL, stored in PostgreSQL, or supplied to browser JavaScript.

The gateway currently supports RFB 3.3, 3.7, and 3.8 with classic VNC authentication type 2. That legacy mechanism uses at most eight password bytes, so the gateway rejects longer values rather than silently truncating them. It must run over the hospital VPN/isolated network because classic RFB authentication does not encrypt the desktop stream. Confirm the hospital VNC product can require this mode. If it supports VeNCrypt, RealVNC RSA-AES, or another stronger security type, add that upstream authentication method before production; never fall back to browser-side credentials.

Doctor and local-operator messages are persisted with the remote-session audit data. Either side can end the session. Ending the session causes the gateway to close the live connection within approximately one second. There is no pause feature.

## Server configuration

1. Copy `deployment/vnc-targets.example.json` to the gitignored `deployment/vnc-targets.json` and set the machine code, private/VPN hostname, and VNC port. Do not add a password to this JSON.
2. Create a host file containing only the VNC password, restrict it to the deployment administrator, and set `SIRIUS_VNC_PASSWORD_FILE` to its absolute path.
3. Start with both compose files:

   `docker compose -f docker-compose.yml -f docker-compose.remote-access.yml up --build`

4. Terminate TLS at the hospital reverse proxy. WebSocket upgrades for `/api/remote-sessions/*/rfb` must be permitted. The included nginx configuration handles the upgrade between the frontend and backend containers.

The old `docker-compose.novnc.yml` websockify service is not used by the secure flow because stock websockify does not terminate VNC authentication.

## Windows agent pilot

Build the separate modern and legacy artifacts on the Windows 11 development laptop:

`cd windows-agent; .\Bootstrap-Legacy-Go.ps1; .\Build-Agent.ps1`

The outputs are `modern-amd64`, `legacy-amd64`, and `legacy-386`. The Go 1.20 legacy builds are lab-only until they pass runtime testing on the exact Windows 7 SP1/8/8.1 OS and architecture. See `docs/windows-legacy-compatibility-audit.md`.

From an elevated PowerShell window on the isolated test PC:

`windows-agent\Install-Agent.ps1 -Server https://dashboard.test.hospital -MachineCode SIRIUS-1 -UIUser HOSPITAL\DiagnosticOperator`

Use `-Legacy` only for the Windows 7/8/8.1 test matrix.

Paste the one-time agent key returned by `POST /api/machines/{id}/agent-credentials` into the hidden console prompt. The installer:

- protects the service configuration with machine-bound Windows DPAPI;
- restricts the service file ACL to SYSTEM and Administrators;
- installs an automatic-start LocalSystem Windows service;
- creates a separate at-logon WPF notification/message UI for the named operator;
- gives that UI only a random loopback-control token, never the machine agent key; and
- listens for UI control only on `127.0.0.1:19377`.

The service reports `locked`, `unlocked`, or `signed_out` using Windows session-change notifications and active-console inspection. After an unexpected service restart while a user is already signed in, it reports `unavailable` until Windows emits the next logon/lock/unlock event, rather than guessing whether the desktop is locked. It automatically accepts an authorized dashboard session without local approval. The per-user UI becomes visible for an active session, displays doctor messages, can reply, and has a local End Session button.

Production deployment must Authenticode-sign both executables and the WPF script (or replace the script with a signed native UI), configure outbound firewall access only to the dashboard, and run the server endpoint over HTTPS.

## Windows authentication proof of concept

The native `windows-auth-poc.exe` calls the documented `LogonUserW` API with `LOGON32_LOGON_INTERACTIVE`. It can prove that the dedicated test account is valid and optionally persist it as a DPAPI machine-bound, SYSTEM/Administrators-only blob without putting the password on a command line:

`build\windows-auth-poc.exe --username DiagnosticRemote --domain . --store C:\ProgramData\HospitalRemoteAgent\windows-credential.dat`

This validation deliberately does not change Winlogon, autologon, or Credential Provider registration.

For an already signed-in but locked diagnostic session, the supported automatic-unlock design is a custom Credential Provider tile. Windows 7 requires a V1 provider; Windows 8/8.1 and newer can use V2. When an authorized remote session is active, the provider retrieves the DPAPI-protected account through a SYSTEM-only local broker, returns password serialization to LogonUI, and requests authentication for that tile. Winlogon/LSA—not noVNC or the service—performs authentication. The standard Microsoft password provider remains registered for recovery. Building and registering those Win32/x64 DLLs requires Visual Studio C++ Build Tools plus the matching Windows SDK and must be done only on the isolated test PC.

For reboot/signed-out operation, first choose between:

- the same custom Credential Provider, triggered only by a short-lived authorized remote grant; or
- Sysinternals Autologon for a physically secured dedicated workstation, if hospital policy accepts its always-on console-logon behavior.

The credential-provider option is the safer match for on-demand access. The current repository validates the credential and secure local storage but does not yet contain or register either Credential Provider DLL. Do not enable Sysinternals Autologon on a hospital PC until the test-PC risk review is signed off.

## Test-PC acceptance sequence

1. Install a service-mode VNC server and require a unique VNC password.
2. Provision a non-administrator dedicated Windows account that has interactive-logon rights and permission to run the diagnostic software.
3. Run `windows-auth-poc.exe` and confirm Windows accepts that account.
4. Install the agent and confirm its Windows service is Running after reboot.
5. Test unlocked, locked, signed-out, and rebooted states separately.
6. During each test, verify the local notification, two-way messages, local End Session, doctor End Session, ticket replay rejection, and immediate loss of keyboard/mouse after termination.
7. Confirm the diagnostic software and current examination survive lock/unlock without data loss before enabling remote control for clinical use.
