# Windows 7/8/8.1 remote-access compatibility audit

Date: 2026-10-24

## Decision

Keep the backend, VNC gateway, noVNC viewer, database, and TLS proxy on the modern Docker laptop. Only the machine agent, notification UI, VNC server, and future Credential Provider run on the diagnostic PC.

Produce three agent artifacts from the same source:

| Artifact | Toolchain | Intended OS and architecture | Status |
| --- | --- | --- | --- |
| `modern-amd64` | Installed supported Go release | Windows 10/11 x64 | Build/test target |
| `legacy-amd64` | Go 1.20.14 | Windows 7 SP1/8/8.1 x64 | Lab-only until tested on each real OS |
| `legacy-386` | Go 1.20.14 | Windows 7 SP1/8/8.1 x86 | Lab-only until tested on each real OS |

Go 1.20 is the final Go release that runs on Windows 7 and Windows 8; Go 1.21 requires Windows 10 or Server 2016. The legacy build is therefore possible, but Go 1.20 is outside the Go security-support window. It cannot be called a production-supported secure runtime in 2026. Sources: [Go 1.20 release notes](https://go.dev/doc/go1.20#ports), [Go Windows support matrix](https://go.dev/wiki/Windows), and [Go security guidance](https://go.dev/doc/security/best-practices).

## Component findings

| Component | Oldest API-level OS | Architecture | Audit result |
| --- | --- | --- | --- |
| Agent HTTP/session logic | Determined by Go runtime | x86/x64 | Source now uses Go 1.20-compatible `ServeMux` routing and requires TLS 1.2 or newer. |
| `golang.org/x/sys/windows` | API-dependent | x86/x64 | Modern builds retain `v0.37.0`. The separate legacy manifest pins `v0.17.0`, whose module requires Go 1.18 and contains the SCM, WTS, DPAPI, and console APIs used here. |
| Windows service/SCM | Windows versions older than the target set | x86/x64 | Service installation and session-change handling use established SCM APIs. Actual service execution remains to be tested on each legacy OS. |
| WTS session state | Windows Vista | x86/x64 | `WTSQuerySessionInformationW`, active-console lookup, and lock/unlock event values cover Windows 7/8/8.1. [WTS documentation](https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ne-wtsapi32-wts_info_class). |
| DPAPI machine-scope storage | Windows XP | x86/x64 | `CryptProtectData`/`CryptUnprotectData` are available. Machine scope protects at rest but the ACL remains mandatory because any local account with blob read access could ask DPAPI to decrypt it. [DPAPI documentation](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata). |
| Credential validation PoC | Windows XP | x86/x64 | `LogonUserW` validates an interactive credential but does not unlock or create the interactive desktop. [LogonUserW documentation](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-logonuserw). |
| Notification UI | Windows 7 with PowerShell 2 and .NET 3.5/WPF | x86/x64 | Removed PowerShell 3-only `Invoke-RestMethod`/JSON cmdlet dependency. It now uses `HttpWebRequest` and `JavaScriptSerializer`. Syntax/runtime still requires an actual Windows 7 test. |
| UI startup | Windows 7 | x86/x64 | Installer uses the selected user profile’s Startup folder. It does not use the Windows 8+ `ScheduledTasks` module and does not store that user’s Windows password. |
| TightVNC server 2.8.90 | Vendor states Windows XP and later | Separate 32/64-bit installers | Service mode and Ctrl+Alt+Delete are required. [Download/support page](https://www.tightvnc.com/download/) and [service-mode guide](https://www.tightvnc.com/doc/win/TightVNC_for_Windows-Installation_and_Getting_Started.pdf). |
| Browser/noVNC | Runs on doctor/server laptop | Modern browser | No downgrade is required or permitted for a legacy diagnostic PC. |

## VNC security boundary

The current gateway supports standard RFB 3.3/3.7/3.8 security type 2. TightVNC documents that this limits the password to eight characters and leaves desktop traffic unencrypted. The test PC firewall therefore accepts TCP 5900 only from the Docker laptop, and the two laptops must be on an isolated test LAN/VLAN. [TightVNC security FAQ](https://www.tightvnc.com/faq.php#howsecure).

Unsupported combinations:

- VNC TCP 5900 exposed to the Internet, hospital-wide user VLAN, or Wi-Fi clients other than the gateway.
- A legacy PC that cannot negotiate TLS 1.2 with the dashboard.
- Unpatched Windows 7 without SP1.
- Any production deployment that depends on an EOL Go 1.20 binary without a hospital-approved exception and isolation controls.
- Using a different Windows account to “unlock” an existing diagnostic session. It creates/switches sessions instead of revealing the intended desktop.

## Interactive Windows authentication split

The authentication component must be separate from the agent and VNC gateway:

- Windows 7 requires a V1 provider implementing `ICredentialProvider` and `ICredentialProviderCredential`; its unlock scenario is `CPUS_UNLOCK_WORKSTATION`.
- Windows 8/8.1 can use a V2 provider. `ICredentialProviderCredential2` has Windows 8 as its minimum client. [Interface requirements](https://learn.microsoft.com/en-us/windows/win32/api/credentialprovider/nn-credentialprovider-icredentialprovidercredential2).
- Both Win32 and x64 DLL builds are required; the DLL architecture must match LogonUI on the installed OS.
- The provider must serialize a credential only when a short-lived authorized session grant is present, use the account owning the locked session, and leave Microsoft’s password provider enabled for recovery.

Microsoft explicitly recommends retaining a system credential provider because a broken third-party provider can otherwise lock users out. [Credential Provider guidance](https://learn.microsoft.com/en-us/windows/win32/secauthn/credential-providers-in-windows).

No Credential Provider DLL is implemented yet. Automatic unlock/sign-in is therefore not complete on any Windows version.

## Evidence and remaining OS matrix

Completed on the development Windows 11 x64 laptop:

- Current-Go tests and native DPAPI round-trip test.
- Modern x64 build with Go 1.27.3 and `x/sys` v0.37.0.
- Legacy x64 and x86 builds with Go 1.20.14 and `x/sys` v0.17.0.
- All three agent executables loaded and reached their command dispatcher on Windows 11 x64.
- PowerShell syntax parsing under the installed Windows PowerShell; PowerShell 2 runtime behavior remains unverified.

Required before claiming compatibility:

| OS | x86 | x64 | Required physical/VM test |
| --- | --- | --- | --- |
| Windows 7 SP1 | Yes | Yes | Install, boot service, DPAPI restart round-trip, WPF UI, WTS lock events, TLS heartbeat, TightVNC secure desktop |
| Windows 8 | Yes | Yes | Same matrix |
| Windows 8.1 | Yes | Yes | Same matrix |
| Windows 10/11 | Not planned | Yes | Existing modern agent plus full two-laptop workflow |

Cross-compilation is build evidence only. Each cell remains “unverified” until its binary is run on that OS/architecture.
