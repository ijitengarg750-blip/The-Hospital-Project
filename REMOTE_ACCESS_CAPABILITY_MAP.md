# Capability Map: Secure Remote Diagnostic Sessions

An authorized doctor must be able to select an examination and open the correct
diagnostic PC without entering a VNC password or waiting for local approval. The
dashboard must coordinate that access without placing reusable Windows or VNC
passwords in URLs, JavaScript, browser storage, application logs, or PostgreSQL.
noVNC remains the browser viewer; it is not an operating-system unlock mechanism.

| Module id | Responsibility | Depends on |
| --- | --- | --- |
| `session-control` | Create authenticated, short-lived, machine-specific sessions; enforce state transitions, expiry, revocation, one active controller, replay protection, cleanup, and audit history. | Existing dashboard authentication and machine registry |
| `machine-agent` | Use a Windows service for machine identity, startup, and termination enforcement, plus a user-session app for the visible remote-session notice, doctor identity, messaging, and local End Session control. Report online, locked, signed-out, busy, and unavailable states. | `session-control` |
| `remote-gateway` | Issue a one-time connection grant, route the WebSocket only to the authorized machine, and perform VNC authentication on the trusted side; never expose the reusable VNC secret to the browser. | `session-control`, `machine-agent` |
| `windows-access` | Use a supported service-mode VNC server for lock/logon desktops. Evaluate a hospital-managed dedicated account and approved Autologon only for never-logged-in/reboot recovery. Consider a custom Credential Provider only if evidence proves it necessary. | `machine-agent`, `remote-gateway` |
| `session-experience` | Show connecting, connected, ending, ended, expired, and unavailable states; embed the live viewer; provide meaningful errors, two-way messages, and End Session on both sides. There is no approval, waiting-for-approval, or pause feature. | `session-control`, `machine-agent`, `remote-gateway` |
| `operations` | Install/start services, restrict the network, rotate device credentials, monitor health, recover after reboot/outage, and provide a kill switch and incident evidence. | All modules |

Build order: `session-control` -> `machine-agent` -> `remote-gateway` ->
`windows-access` -> `session-experience` -> `operations`.

## Non-negotiable real-world cases

- PC is off, asleep, booting, offline, locked, already in use, or has no logged-in
  user; the dashboard must show the actual state and fail closed.
- A local operator can see who is connected, send a message, and end an active
  session immediately. Local termination always overrides remote control.
- A remote operator can end the session; disconnect,
  browser close, agent restart, network partition, or expiry must revoke control.
- A stale link, copied token, duplicate command, or request for another machine
  must not start or resume a session.
- Normal Windows authentication remains in force. The system must not simulate
  typing a stored password, bypass authentication, or silently unlock an
  unrelated user's session.
- Emergency termination works locally even when the dashboard server or network
  is unavailable.
- Every request, approval/rejection, connection, message metadata, unlock result,
  disconnect, timeout, and termination is timestamped without logging message
  contents, passwords, tokens, or patient data.
- Windows 7 compatibility is treated separately: modern security primitives or a
  supported VNC server may be unavailable, so unsupported machines remain
  manual-password or view-only rather than receiving a weaker automatic bypass.

## First safe delivery slice

Implement `session-control` and a simulated `machine-agent` first. This proves
machine identity, authorization, one-time grants, expiry, termination, race
handling, and audit behavior without handling a real Windows password. Next add
gateway-side VNC authentication, followed by local notification/messaging, then
real-machine lock/logon/restart testing. Windows authentication integration is
considered only after the exact Windows version, VNC server, account arrangement,
and vendor software behavior are recorded.

