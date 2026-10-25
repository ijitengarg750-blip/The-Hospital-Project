# Windows access recommendation

This document separates VNC authentication from Windows authentication. They are
different trust boundaries and must not share a browser-visible credential.

## Recommended architecture

1. The doctor authenticates to the dashboard and receives a short-lived,
   machine-specific, single-use connection grant.
2. The trusted gateway validates that grant and opens a mutually authenticated
   TLS connection to the enrolled machine agent. The agent authenticates to a
   loopback VNC server. The browser and backend never receive the reusable VNC
   password or agent private key.
3. A machine service reports state and can force the VNC connection closed. A
   user-session application provides the visible notice, messages, and local End
   Session button.
4. The VNC server binds to loopback. The host firewall permits the mTLS agent
   port only from the gateway. Certificate or TLS failure closes the connection
   without a plaintext fallback.

noVNC's API accepts a password in the browser client. That is suitable for manual
entry but not for this design because JavaScript would receive the reusable
secret. Automatic authentication therefore belongs inside the diagnostic-PC
agent, not in a modified noVNC page or server-side password file.

## Windows state matrix

| Machine state | Recommendation |
| --- | --- |
| Unlocked; diagnostic software running | Connect through a service-mode VNC server. Show the local remote-session notice and do not interrupt an active examination. |
| Locked; existing user session running | Use a VNC server whose current version explicitly supports the Windows lock/logon desktop in service mode. Keep the session locked; the doctor must use an approved Windows sign-in method if authentication is required. Do not type or inject a stored Windows password. |
| Booted; nobody has signed in | Service-mode VNC may expose the Windows sign-in screen, but Windows authentication still applies. Prefer a hospital-managed dedicated workstation account and manual/approved sign-in. |
| Signed out | Treat this like the Windows sign-in screen. Do not silently enter credentials or attach to another user's future session. |
| Restarted after power failure | Start the machine service and service-mode VNC automatically. If the diagnostic software requires an interactive desktop, hospital IT may approve Sysinternals Autologon for a dedicated, physically controlled workstation after accepting its risks. Test vendor software startup separately. |

## Locked-session recommendation

Do not unlock Windows automatically. Keep the existing session locked and use a
service-mode VNC product that can display the secure desktop. If the clinical
workflow truly requires an unlocked desktop without local action, use an existing
hospital-managed Windows authentication method. A custom Credential Provider is
not justified until the exact OS, account type, VNC product, and policy constraints
show that supported methods cannot meet the requirement.

## Never-logged-in recommendation

First test service-mode VNC access to the normal Windows sign-in screen. If the
workstation is dedicated to one diagnostic application and must recover
unattended after power failure, hospital IT can evaluate Sysinternals Autologon
for a dedicated least-privilege account with BitLocker, physical controls, network
isolation, and documented credential rotation. Autologon applies at boot/logon;
it is not a mechanism for unlocking an already locked session.

Microsoft warns that Autologon stores the password as an LSA secret that a local
administrator can retrieve and that physical access effectively grants access to
the configured account. It must not be enabled by this project without explicit
machine-specific approval.

## Evidence still required from the diagnostic PC

- Exact Windows edition, version/build, architecture, and support status.
- Exact VNC server product/version, license, service-mode behavior, encryption,
  authentication options, and command/API support for disconnecting a client.
- Whether the PC is domain/Entra joined or standalone and which account runs the
  diagnostic software.
- Whether the vendor software survives lock, logoff, user switching, and reboot;
  whether an examination can be active when remote access begins.
- Whether BitLocker, Secure Boot, Credential Guard, Windows Firewall, and a
  hospital VPN or isolated VLAN are available.
- A supervised test of unlocked, locked, signed-out, restart, outage, concurrent
  access, and local emergency termination behavior.

## Official references

- noVNC API: https://novnc.com/noVNC/docs/API.html
- Microsoft Credential Providers: https://learn.microsoft.com/en-us/windows/win32/secauthn/credential-providers-in-windows
- Microsoft Winlogon and Credential Providers: https://learn.microsoft.com/en-us/windows/win32/secauthn/winlogon-and-credential-providers
- Microsoft Sysinternals Autologon: https://learn.microsoft.com/en-us/sysinternals/downloads/autologon
- Microsoft automatic-logon security warning: https://learn.microsoft.com/en-us/troubleshoot/windows-server/user-profiles-and-logon/turn-on-automatic-logon
- TightVNC service-mode overview (candidate only; verify the actual installed VNC product): https://www.tightvnc.com/winst.php

