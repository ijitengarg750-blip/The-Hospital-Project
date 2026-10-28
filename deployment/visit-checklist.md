# Diagnostic Machine Onboarding Record

Complete one copy per machine.

## Machine details

- Machine code:
- Manufacturer/model:
- Modality:
- Room/location:
- Windows version:
- 32-bit or 64-bit:
- PC IP address:
- Server reachable from PC: Yes / No

## Export investigation

- Automatic export folder:
- Formats observed:
- DICOM PatientID equals Practo UID: Yes / No / Not applicable
- Non-DICOM UID location/pattern:
- One examination creates how many files:
- Files stop changing after approximately:

## Collection validation

- Machine registered on server: Yes / No
- Collector configuration saved: Yes / No
- Test export detected automatically: Yes / No
- Upload retried after temporary network interruption: Yes / No
- Duplicate export avoided: Yes / No
- Correct patient UID shown: Yes / No
- Correct examination time shown: Yes / No
- Report opens: Yes / No
- Images open: Yes / No / Not available
- Video plays: Yes / No / Not available

## Remote access validation

- VNC server installed/configured: Yes / No
- TightVNC bound to loopback only: Yes / No
- Outbound production mode has no inbound TCP 5910 firewall rule: Yes / No
- Stable dashboard hostname and trusted certificate validated: Yes / No
- DHCP address changed without server configuration update: Yes / No
- Wi-Fi outage/reconnection recovered automatically: Yes / No
- Offline local End Session reconciled after connectivity returned: Yes / No
- Remote Connector crash/restart recovered independently: Yes / No
- Data Collector crash/restart recovered independently: Yes / No
- TightVNC crash/restart did not stop unrelated components: Yes / No
- Notification UI crash/restart recovered independently: Yes / No
- Server restart reconciled stale sessions: Yes / No
- Certificate rotation overlap and revocation tested: Yes / No
- Final signed binaries authorized by WDAC policy: Yes / No
- Changed deployment scripts re-signed and verified: Yes / No
- Phase 1 inbound path used only for explicit lab comparison: Yes / No
- noVNC URL configured: Yes / No
- Open Live Software works: Yes / No
- Remote password is not stored in dashboard/PostgreSQL: Confirmed / Not confirmed

## Startup and recovery

- Dedicated low-privilege collector account used: Yes / No
- Automatic startup task installed: Yes / No
- PC restarted and collector resumed: Yes / No
- Server restarted and dashboard resumed: Yes / No

## Final decision

- Integrated successfully: Yes / No
- Remaining blocker:
- Notes:

