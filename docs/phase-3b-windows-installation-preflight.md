# Phase 3B — Windows installation preflight

## Implemented

`windows-agent/Preflight.ps1` is a read-only component for the current signed PowerShell installer and the future MSI user interface. It emits a stable list of `{Name, Status, Message}` results and, with `-Json`, machine-readable JSON. The only statuses are `Passed`, `Warning`, `Failed`, and `Not Tested`. Exit code 1 means at least one result failed; warnings and not-tested results do not hide a failure.

The preflight checks:

- Windows 10/11 and AMD64/x64 support;
- trusted Authenticode signatures and a common signer for the installer, uninstaller, preflight, UI, and agent executable;
- WDAC/App Control authorization by running only the agent's side-effect-free `preflight` command, and only after signature validation passes;
- DNS, outbound TCP 443, and trusted HTTPS/WSS TLS to the configured dashboard;
- clean, complete, or partial existing installation/enrollment state without reading or returning credentials;
- TightVNC service state and runtime loopback binding;
- elevation and unsafe broad write access on existing agent directories; and
- existing connector service account, start mode, executable path, and added service dependencies.

TightVNC authentication is deliberately `Not Tested` before secure enrollment because the preflight does not accept or expose the VNC password. An open port is not reported as successful authentication.

The signed `Install-Agent.ps1` invokes the signed preflight before copying files, starting enrollment, writing configuration, or changing services. A failed preflight therefore cannot partially enroll the PC or overwrite an existing installation. The preflight contains no install, enrollment, removal, firewall, App Control, ACL, or security-policy mutation.

Example for a future graphical installer:

```powershell
powershell.exe -NoProfile -ExecutionPolicy AllSigned -File .\Preflight.ps1 `
  -Server https://dashboard.example -Json
```

## Verified in development

- Focused Pester tests cover the four-state contract, supported platform boundary, loopback-only classification, existing/partial installation states, unsafe server URL handling, failure exit behavior, and the read-only contract.
- Installer Pester tests verify preflight runs and fails before the first copy or enrollment call, and that packaging includes the preflight in the signed script set.
- Go tests cover the no-op `preflight` command and reject unexpected arguments.
- A local failed-preflight run produced 11 parseable JSON results and exit code 1 without installing, enrolling, deleting, or changing policy.

## Not yet verified

- The current checked-in pilot ZIP predates Phase 3B and was not rebuilt or re-signed. Final package signing remains a Phase 4 gate.
- Authorization under a hospital's real WDAC/App Control policy requires its final signed artifact and policy on the target PC.
- Trusted TLS, DNS, and port 443 require the real hospital network and production certificate chain.
- Elevated filesystem/service checks and TightVNC loopback behavior require the target diagnostic PC.
- TightVNC authentication remains a post-enrollment validation, not a pre-enrollment claim.
- No unsigned service was installed or run, and no firewall or Windows security policy was changed during automated testing.

Phase 3C remains responsible for the maintainable MSI install/upgrade/repair/rollback/uninstall workflow.
