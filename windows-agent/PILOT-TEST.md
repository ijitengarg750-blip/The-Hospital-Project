# Hospital Remote Agent pilot package

This package is for a supervised Windows 11 functional test with synthetic data. It is not approval for production or hospital deployment.

## Before installation

1. Confirm every file listed in `SHA256SUMS.txt` matches its SHA-256 hash.
2. Confirm each executable and PowerShell script has a valid Authenticode signature from `Hospital Diagnostic Dashboard Pilot`.
3. Import `HospitalDashboardPilot.cer` only on the agreed test PC, into the machine `Trusted Root Certification Authorities` and `Trusted Publishers` stores.
4. Install an approved service-mode VNC server and restrict it to `127.0.0.1:5900` with a unique test-only password of 1-8 bytes.
5. Register the test machine in the dashboard and open a fresh enrollment window.
6. Confirm the intended UI account exists. The installer registers a per-user notification task; a pre-existing profile is not required.

Run `Preflight.ps1 -Server https://dashboard.example -Json` from an elevated PowerShell window first. Exit code 1 means at least one required check failed; warnings and not-tested checks remain visible for administrator review. The installer runs the same signed preflight automatically and stops before copying files or enrolling when it fails.

Run `Install-Agent.ps1` from an elevated PowerShell window with the dashboard HTTPS URL, registered machine code, and UI account. The agent shows a verification code for administrator approval and prompts for the VNC password without echoing it.

The packaged Phase 1 executable remains an inbound, lab-only comparison artifact. Do not expose ports 5900 or 5910 to the internet. If port 5910 is required for that lab comparison, restrict it to the dashboard server's private IP. Phase 2 outbound mode must not install or retain an inbound 5910 rule.

The self-signed certificate proves package integrity only after the public certificate has been deliberately trusted. It does not prove that the code is safe, does not override WDAC/App Control, and is not a production identity.
