# Two-laptop encrypted remote-access test

This is a supervised, non-production acceptance run using synthetic data and a test-only Windows/VNC account. It does not install or configure a VPN.

## Security boundary

```text
Doctor browser -- HTTPS/WSS --> dashboard gateway -- mutual TLS --> machine agent -- loopback RFB --> VNC server
```

The machine agent is the only process allowed to know the reusable VNC password. It stores the password, agent private key, and API credential in a LocalMachine DPAPI blob. The browser and server never receive them. The gateway verifies the enrolled agent certificate; the agent requires a gateway client certificate. Either certificate/configuration failure closes the connection—there is no plaintext fallback.

## 1. Record addresses and build the agent

Laptop A is the Windows 11 Docker/dashboard host. Laptop B simulates the diagnostic PC. Record both private-LAN addresses, then on Laptop A:

```powershell
Set-Location D:\hospital-diagnostic-dashboard\windows-agent
Set-ExecutionPolicy -Scope Process Bypass
.\Bootstrap-Legacy-Go.ps1
.\Build-Agent.ps1
Get-Content .\build\SHA256SUMS.txt
```

Use `modern-amd64` on Windows 10/11 x64. The `legacy-amd64` and `legacy-386` artifacts are lab-only Windows 7 SP1/8/8.1 builds; cross-compilation is not OS certification. Copy the required artifact and installer directory to Laptop B using trusted media and compare SHA-256 hashes.

## 2. Prepare Laptop A

Create `.env` if needed, then generate separate HTTPS and gateway mTLS identities:

```powershell
Set-Location D:\hospital-diagnostic-dashboard
python .\setup-local.py --init
.\deployment\Prepare-TwoLaptopServer.ps1 -ServerIP $ServerIP -TestPcIP $TestPcIP -MachineCode SIRIUS-TEST-01
certutil -addstore -f Root .\deployment\test-tls\ca.crt
docker compose -f docker-compose.yml -f docker-compose.remote-access.yml -f docker-compose.two-laptop.yml up -d --build
Invoke-WebRequest "https://${ServerIP}:8443/api/health" -UseBasicParsing
```

The generated private keys are test-only, gitignored, and ACL-restricted. Do not copy `gateway-client.key`, `gateway-ca.key`, or any VNC secret to Laptop B.

## 3. Install VNC locally on Laptop B

Install an approved service-mode VNC server matching the OS architecture. For the current TightVNC lab candidate, enable server/service mode, disable its automatic firewall exception, require a test-only 1–8 byte primary password, and bind/listen on loopback if the product supports it. Never place the password on a command line.

Confirm only the local endpoint is required:

```powershell
net start tvnserver
Test-NetConnection 127.0.0.1 -Port 5900
```

The dashboard must not connect to TCP 5900. Do not create a LAN/public firewall rule for it.

## 4. Register, grant, and enroll

Sign in to the dashboard as an administrator. In **Diagnostic machines**:

1. Register `SIRIUS-TEST-01`, with Laptop B's private IP, port `5910`, and TLS server name `SIRIUS-TEST-01`.
2. Grant the test doctor access.
3. Select **Provision / rotate** and copy the one-time enrollment token. It expires after 10 minutes and is invalid after one use.

Alternatively, `Provision-TestMachine.ps1` performs registration and prints only that enrollment token.

On Laptop B, create a dedicated test operator, sign in once so its profile exists, then run elevated:

```powershell
Set-Location D:\copied\windows-agent
.\Install-Agent.ps1 -Server "https://${ServerIP}:8443" -MachineCode SIRIUS-TEST-01 -UIUser "$env:COMPUTERNAME\HospitalTestOperator"
```

Add `-Legacy` only for a corresponding lab build. Paste the enrollment token and local VNC password at the two hidden prompts. Installation generates the agent identity on Laptop B and sends only its public certificate to the server.

## 5. Firewall rule (IT-owned, least exposure)

Hospital IT must allow the agent tunnel only from Laptop A to Laptop B. Run elevated on Laptop B:

```powershell
netsh advfirewall firewall add rule name="Hospital mTLS Agent Test" dir=in action=allow protocol=TCP localport=5910 remoteip=$ServerIP profile=private
```

Do not expose 5900 or 5910 to the internet. This repository does not deploy a VPN; use the hospital's approved VPN/VLAN/firewall controls if remote routing is required.

## 6. Acceptance sequence

From the admin screen, **Test connection** must report reachable. Then sign in as the granted doctor and open a synthetic study:

1. The desktop appears without a browser password prompt.
2. The local notice identifies the session and messages flow both ways.
3. Local or doctor-side **End session** closes control promptly.
4. A second concurrent session is rejected.
5. Revoking the machine invalidates the agent key and ends active sessions.

Repeat while Windows is unlocked, locked, signed out, and after reboot. Locked/signed-out tests prove transport and secure-desktop visibility only; this project does not inject Windows credentials or implement automatic unlock.

## 7. Fail-closed tests

Perform each test independently and restore the file/service afterward:

- stop the agent: connection test is unreachable;
- remove the backend gateway client certificate: connection test is unreachable;
- change the configured TLS server name: certificate verification fails;
- attempt plaintext TCP on 5910: no tunnel-ready preface or VNC bytes are returned;
- reuse an enrollment token: enrollment returns unauthorized;
- remove the doctor's grant: patient/study and remote-session routes deny access.

At no point should the gateway retry TCP 5900 or another unencrypted route.

## Troubleshooting and rollback

```powershell
docker compose -f docker-compose.yml -f docker-compose.remote-access.yml -f docker-compose.two-laptop.yml logs --tail 200 backend frontend
sc.exe query HospitalRemoteAgent
netstat -ano | findstr :5910
```

For foreground agent diagnostics, stop the service and run its `console` command with the DPAPI configuration, then restart the service. Logs must not contain the enrollment token, agent key, private key, VNC password, or patient content.

Rollback Laptop B with `Uninstall-Agent.ps1 -RemoveConfiguration`, delete the named 5910 firewall rule, and remove the test VNC server/account. On Laptop A, stop the three-file Compose stack and remove the test HTTPS CA from the trust store. Delete `deployment/test-tls` only after the run; it cannot be recovered and must never contain production identity material.
