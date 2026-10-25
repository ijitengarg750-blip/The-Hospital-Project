# Two-laptop one-click remote-access test

This guide uses only an isolated test account, synthetic/existing local test studies, and a dedicated VNC password. Do not use patient data or a hospital production PC.

## Supported test topology

- **Laptop A:** Windows 11, this repository, Docker Desktop, doctor browser, backend, gateway, database, and TLS endpoint.
- **Laptop B:** the simulated diagnostic PC, connected to the same private test network.
- TCP 8443 flows from Laptop B and the doctor browser to Laptop A.
- TCP 5900 flows only from Laptop A to Laptop B.

For the first complete run, use Windows 10/11 x64 on Laptop B. Windows 7/8/8.1 artifacts can then be exercised with the same sequence, but must not be marked compatible until run on the corresponding real OS and architecture.

## Downloads

Laptop A needs the already-used Docker Desktop and a current supported Go toolchain. `windows-agent/Bootstrap-Legacy-Go.ps1` downloads the official Go 1.20.14 ZIP and verifies its published SHA-256 before legacy builds.

Laptop B needs TightVNC 2.8.90:

- x64: `https://www.tightvnc.com/download/2.8.90/tightvnc-2.8.90-gpl-setup-64bit.msi`
- x86: `https://www.tightvnc.com/download/2.8.90/tightvnc-2.8.90-gpl-setup-32bit.msi`

Use the installer matching the operating system architecture. Do not use TightVNC 1.x; its service mode does not support Windows 7/8/8.1.

## 1. Record the two LAN addresses

On Laptop A, run and enter the two addresses shown by `ipconfig` when prompted:

```powershell
ipconfig
$ServerIP = Read-Host 'Laptop A IPv4 address'
$TestPcIP = Read-Host 'Laptop B IPv4 address'
Test-Connection $TestPcIP -Count 2
```

On Laptop B:

```powershell
ipconfig
$ServerIP = Read-Host 'Laptop A IPv4 address'
$TestPcIP = Read-Host 'Laptop B IPv4 address'
Test-Connection $ServerIP -Count 2
```

Use a private test network and set its Windows network profile to Private. Keep these PowerShell windows open so the variables remain available for later steps.

## 2. Build all agent artifacts on Laptop A

From an ordinary PowerShell window in the repository:

```powershell
Set-Location D:\hospital-diagnostic-dashboard\windows-agent
Set-ExecutionPolicy -Scope Process Bypass
.\Bootstrap-Legacy-Go.ps1
.\Build-Agent.ps1
Get-Content .\build\SHA256SUMS.txt
```

Outputs:

- `build\modern-amd64\` for Windows 10/11 x64.
- `build\legacy-amd64\` for Windows 7 SP1/8/8.1 x64 lab testing.
- `build\legacy-386\` for Windows 7 SP1/8/8.1 x86 lab testing.

Copy the complete `windows-agent` directory to Laptop B using trusted removable media. Re-run `Get-FileHash -Algorithm SHA256` on Laptop B and compare it with `SHA256SUMS.txt`.

## 3. Prepare TLS, VNC target, and secrets on Laptop A

Ensure `.env` already exists. If this is a new local installation only:

```powershell
Set-Location D:\hospital-diagnostic-dashboard
python .\setup-local.py --init
```

Run the preparation script with the two addresses recorded in step 1:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\deployment\Prepare-TwoLaptopServer.ps1 -ServerIP $ServerIP -TestPcIP $TestPcIP -MachineCode SIRIUS-TEST-01
```

Enter a test-only VNC password of at most eight Latin-1 characters. Enter the identical value later in TightVNC. The script does not print it. It creates:

- a 30-day local test CA and server certificate under the gitignored `deployment\test-tls` directory;
- a protected, gitignored VNC password file;
- `deployment\vnc-targets.json`, pointing `SIRIUS-TEST-01` to Laptop B; and
- the required `.env` origin and Docker-secret path.

Trust the test CA on Laptop A:

```powershell
certutil -addstore -f Root .\deployment\test-tls\ca.crt
```

Now copy `deployment\test-tls\ca.crt` to Laptop B using the same trusted media.

Open inbound TLS only for Laptop B on Laptop A from an elevated prompt:

```powershell
netsh advfirewall firewall add rule name="Hospital Remote Test TLS" dir=in action=allow protocol=TCP localport=8443 remoteip=$TestPcIP profile=private
```

Start the real gateway stack:

```powershell
docker compose -f docker-compose.yml -f docker-compose.remote-access.yml -f docker-compose.two-laptop.yml up -d --build
Invoke-WebRequest "https://${ServerIP}:8443/api/health" -UseBasicParsing
```

The health request must return HTTP 200 without a certificate warning.

## 4. Install and configure TightVNC on Laptop B

From an elevated prompt in the directory containing the downloaded MSI, run the command matching Laptop B:

```powershell
msiexec.exe /i .\tightvnc-2.8.90-gpl-setup-64bit.msi ADDLOCAL=Server SERVER_REGISTER_AS_SERVICE=1 SERVER_ADD_FIREWALL_EXCEPTION=0 SERVER_ALLOW_SAS=1
```

or:

```powershell
msiexec.exe /i .\tightvnc-2.8.90-gpl-setup-32bit.msi ADDLOCAL=Server SERVER_REGISTER_AS_SERVICE=1 SERVER_ADD_FIREWALL_EXCEPTION=0 SERVER_ALLOW_SAS=1
```

In the installer UI, configure the **service-mode primary VNC password** using the same test password entered on Laptop A. Do not disable authentication. Do not place the password in an MSI command line.

Restrict VNC to Laptop A:

```powershell
netsh advfirewall firewall add rule name="Hospital Remote Test VNC" dir=in action=allow protocol=TCP localport=5900 remoteip=$ServerIP profile=private
net start tvnserver
netstat -ano | findstr :5900
```

On Laptop A, verify reachability:

```powershell
Test-NetConnection $TestPcIP -Port 5900
```

## 5. Create the test Windows account on Laptop B

From an elevated prompt:

```powershell
net user HospitalTestOperator * /add
```

Enter a test-only Windows password when prompted. Sign in once as `HospitalTestOperator`, then sign back into the administrator account for agent installation. Do not use a real hospital or personal account.

Trust Laptop A’s test CA on Laptop B from an elevated prompt:

```powershell
certutil -addstore -f Root .\ca.crt
```

## 6. Register the machine and install the agent

On Laptop A:

```powershell
Set-Location D:\hospital-diagnostic-dashboard
.\deployment\Provision-TestMachine.ps1 -DashboardUrl "https://${ServerIP}:8443" -MachineCode SIRIUS-TEST-01
```

The script reuses the machine if it already exists and rotates/prints a one-time agent key. The machine code must match the machine attached to the local test study shown in the dashboard; this repository’s established local pilot uses `SIRIUS-TEST-01`.

Create or reuse a text-only synthetic study for that machine, without patient data:

```powershell
python .\deployment\create-test-study.py --dashboard-url "https://${ServerIP}:8443" --machine-code SIRIUS-TEST-01
```

On Laptop B, from an elevated prompt in the copied `windows-agent` directory:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Install-Agent.ps1 -Server "https://${ServerIP}:8443" -MachineCode SIRIUS-TEST-01 -UIUser "$env:COMPUTERNAME\HospitalTestOperator"
```

For Windows 7/8/8.1, add `-Legacy`:

```powershell
.\Install-Agent.ps1 -Server "https://${ServerIP}:8443" -MachineCode SIRIUS-TEST-01 -UIUser "$env:COMPUTERNAME\HospitalTestOperator" -Legacy
```

Paste the one-time agent key at the hidden prompt. Verify:

```powershell
sc.exe query HospitalRemoteAgent
$UiSid = (New-Object Security.Principal.NTAccount "$env:COMPUTERNAME\HospitalTestOperator").Translate([Security.Principal.SecurityIdentifier]).Value
$UiProfile = (Get-WmiObject Win32_UserProfile | Where-Object { $_.SID -eq $UiSid }).LocalPath
Get-ChildItem "$UiProfile\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\HospitalRemoteUI.cmd"
```

Sign in as `HospitalTestOperator` to start the notification UI.

## 7. One-click, messaging, and termination test

On Laptop A, run `Start-Process "https://${ServerIP}:8443"`, sign in with the local test doctor account, open `REMOTE-ACCESS-TEST-PATIENT`, and select **Open Phoenix / live software**.

Expected now:

1. No VNC password is requested in the browser.
2. The existing Laptop B desktop appears.
3. The Laptop B notification window identifies the active session.
4. A doctor message appears on Laptop B.
5. A Laptop B reply appears in the dashboard.
6. **End Session** on either side closes control within about one second.

Repeat once using doctor-side termination and once using Laptop B’s local End Session.

## 8. Locked, signed-out, and reboot tests

### Locked

On Laptop B, start a harmless test application, then run:

```powershell
rundll32.exe user32.dll,LockWorkStation
```

Start another dashboard session. The service-mode VNC server should show the Windows secure desktop and the agent should report `locked`. Automatic credential submission is **not implemented yet**, so stop here without manually typing the Windows password. This test proves transport and secure-desktop visibility, not automatic unlock.

### Signed out

Sign out `HospitalTestOperator`, then start a dashboard session. Verify that TightVNC still exposes the sign-in screen and the agent service remains running. Automatic sign-in is not expected until the Credential Provider is implemented.

### Reboot

Reboot Laptop B and, before anyone signs in, verify from Laptop A:

```powershell
Test-NetConnection $TestPcIP -Port 5900
```

Start a dashboard session and confirm the sign-in screen is visible. Again, successful automatic Windows sign-in is not yet an acceptance result.

## Troubleshooting

Laptop A:

```powershell
docker compose -f docker-compose.yml -f docker-compose.remote-access.yml -f docker-compose.two-laptop.yml ps
docker compose -f docker-compose.yml -f docker-compose.remote-access.yml -f docker-compose.two-laptop.yml logs --tail 200 backend frontend
Test-NetConnection $TestPcIP -Port 5900
Invoke-WebRequest "https://${ServerIP}:8443/api/health" -UseBasicParsing
```

Laptop B:

```powershell
sc.exe query HospitalRemoteAgent
sc.exe query tvnserver
netstat -ano | findstr :5900
$UiSid = (New-Object Security.Principal.NTAccount "$env:COMPUTERNAME\HospitalTestOperator").Translate([Security.Principal.SecurityIdentifier]).Value
$UiProfile = (Get-WmiObject Win32_UserProfile | Where-Object { $_.SID -eq $UiSid }).LocalPath
Get-ChildItem "$UiProfile\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\HospitalRemoteUI.cmd"
certutil -store Root "Hospital Dashboard Test CA"
```

For foreground agent diagnostics, stop the service and run the same installed binary against its DPAPI configuration:

```powershell
net stop HospitalRemoteAgent
& "$env:ProgramFiles\HospitalRemoteAgent\hospital-remote-agent.exe" console "$env:ProgramData\HospitalRemoteAgent\service.dat"
```

Press Ctrl+C afterward and restart it with `net start HospitalRemoteAgent`.

## Uninstall and restore Laptop B

From an elevated prompt in the copied `windows-agent` directory:

```powershell
.\Uninstall-Agent.ps1 -RemoveConfiguration
netsh advfirewall firewall delete rule name="Hospital Remote Test VNC"
certutil -delstore Root "Hospital Dashboard Test CA"
net user HospitalTestOperator /delete
msiexec.exe /x .\tightvnc-2.8.90-gpl-setup-64bit.msi
```

Use the 32-bit MSI filename in the last command on an x86 PC.

Restore Laptop A:

```powershell
docker compose -f docker-compose.yml -f docker-compose.remote-access.yml -f docker-compose.two-laptop.yml down
netsh advfirewall firewall delete rule name="Hospital Remote Test TLS"
certutil -delstore Root "Hospital Dashboard Test CA"
```

Delete the gitignored `deployment\test-tls`, `deployment\test-secrets`, and `deployment\vnc-targets.json` only after the test is finished. This does not remove PostgreSQL test data or unrelated Docker volumes.
