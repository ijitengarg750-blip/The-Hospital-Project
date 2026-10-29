# Phase 3C — Windows MSI installer source

Phase 3C adds the maintainable source and automated checks for one per-machine, x64 MSI containing three independently selectable components: Remote Connector, Data Collector, and Local Notification UI. The MSI source is in `windows-agent/installer` and the graphical post-install configuration shortcut removes the routine need to edit JSON, copy certificates, or enter IP addresses manually.

## Lifecycle behavior

- Clean install stages the signed connector, collector, notification UI, and configuration tools. Enrollment and collector credentials are entered through masked graphical controls after MSI installation.
- Upgrade uses a WiX major upgrade scheduled after `InstallInitialize`, preserves `%ProgramData%` enrollment and collector state, stops the connector around file replacement, repoints its service to the MSI binary, and starts it again.
- Repair restores MSI-managed files and repairs an existing enrolled connector. The connector, collector task, and notification task retain separate recovery behavior.
- Uninstall removes services, tasks, shortcuts, and MSI-managed program files. Enrollment, collector configuration, queue state, and source DICOM files are retained by default. The Start-menu uninstall shortcut explicitly offers removal of the first three; source DICOM files are never deleted.
- TightVNC is detected by preflight but is not packaged, replaced, or reconfigured.

## Build and validation

`Build-Installer.ps1` requires an explicitly installed WiX 4.0.6 tool and fails closed for a different version. It runs both Go test suites, builds fresh connector and collector binaries, stages the payload in a temporary directory, and invokes `wix build` with the WiX UI and Util extensions.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows-agent\installer\Build-Installer.ps1 -Version 0.1.0
```

The design follows the WiX command-line build and extension model, uses `WixUI_FeatureTree`, and uses deferred `WixQuietExec` actions for elevated maintenance. `MajorUpgrade Schedule="afterInstallInitialize"` was selected so Windows Installer can restore the older product if the newer install fails. Relevant primary references: [WiX build command](https://docs.firegiant.com/wix/tools/wixexe/), [WiX standard UI](https://docs.firegiant.com/wix/tools/wixext/wixui/), [WiX MajorUpgrade](https://docs.firegiant.com/wix/schema/wxs/majorupgrade/), [WiX QuietExec](https://docs.firegiant.com/wix/tools/wixext/quietexec/), and [Windows Installer rollback](https://learn.microsoft.com/en-us/windows/win32/msi/rollback-installation).

## Evidence boundary

Confirmed in this phase: source parsing, lifecycle policy assertions, graphical-input security assertions, PowerShell syntax, Windows-agent tests, and collector tests. The checked-in installer sources intentionally do not install WiX or change the development machine toolchain.

Not yet confirmed: compiling an MSI on a machine with the pinned WiX tool, signing every payload and the MSI, WDAC/App Control execution, clean/upgrade/repair/rollback/uninstall on real target PCs, reboot recovery, real collector account rights, real TightVNC authentication, production TLS, and clinical workflow behavior. Those remain Phase 4 release gates. Do not deploy or run unsigned outputs as services.
