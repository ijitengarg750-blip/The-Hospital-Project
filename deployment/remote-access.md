# One-machine noVNC pilot

The dashboard launches an authenticated, audited link to noVNC. noVNC is a
browser VNC client; websockify bridges the browser WebSocket to the PC's VNC
server. The dashboard login does not authenticate the separate VNC service.
References: https://github.com/novnc/noVNC and https://github.com/novnc/websockify.

## Local Windows test PC

1. Confirm the PC has a VNC server listening on TCP 5900. Port 6080 is the
   browser gateway port, not the VNC server port. Keep VNC password authentication
   enabled. Do not include passwords in URLs or configuration committed to Git.
2. From the project root in PowerShell:

   ```powershell
   $env:HOSPITAL_VNC_TARGET = '192.168.1.13:5900'
   docker compose -f docker-compose.novnc.yml up -d --build
   ```

   The gateway listens on localhost only by default. This is sufficient for a
   browser on the test PC. It does not create a VNC server or configure Windows
   startup for that server.
3. Sign in to the dashboard, expand **Remote access setup**, and save this URL
   against the test machine:

   ```text
   http://localhost:6080/vnc.html?autoconnect=true&resize=scale&view_only=true
   ```

4. Find an examination and select **Open Phoenix / live software**. Enter the
   VNC password in noVNC. Verify the expected PC desktop and Phoenix examination.
   The initial link requests view-only operation. Changes in Phoenix must be
   performed deliberately by an authorized operator.

For a different doctor PC, localhost would refer to that doctor's PC. Bind the
gateway to an approved hospital interface using `NOVNC_BIND_ADDRESS`, configure
firewall/VPN and encrypted transport, and save the actual gateway URL instead.
Keep the gateway and VNC server restricted to the approved network. Do not expose
6080 or 5900 to the public internet. Do not treat a saved URL or HTTP 200 as proof
of an authenticated VNC connection.

## Required report views

The supplied Phoenix screenshots define four useful report types:

| Type | Content to compare in the same original examination |
| --- | --- |
| Corneal mapping summary | Thickness, tangential anterior curvature, anterior/posterior elevation, scales and summary indices |
| Corneal thickness | Complete pachymetry map and micrometre scale |
| Tear-film analysis | Ring image, damage stream, break-up map, graph, NIF-BUT and NIAvg-BUT |
| Pupillography | Scotopic/mesopic/photopic/dynamic images, diameters and position/time graphs |

PDF reports and explicitly selected report images appear first. Under **Source
images and original files**, open an image and select its report type only after
confirming it is a complete report. This selection changes presentation metadata;
it does not modify source bytes or validate measurements. It can be undone with
**Move to source images**. No filename heuristic creates clinical maps or graphs.
If the DICOM contains only raw frames or proprietary measurements, export the
complete report from Phoenix as PDF/JPEG/PNG into the collector's configured
export folder. For non-DICOM exports, configure and verify the patient and study
UID regex rules first so the report belongs to the correct examination.

## Acceptance record before expanding beyond one machine

Record date, operator, machine, patient/study identifier (in protected hospital
records), expected result, actual result and evidence for each check. Leave
unexecuted checks pending.

- Real duplicate: upload the same DICOM under two names into an isolated stack;
  compare database object/file counts and stored SHA-256 hashes.
- Network recovery: interrupt the isolated collector-to-server connection during
  upload, restart the collector, restore connectivity, and confirm one completed
  examination with matching source bytes.
- Incomplete file: write a copied DICOM gradually, including a pause longer than
  the stability window. Confirm it is not presented as a complete report. Stable
  size alone cannot guarantee that a vendor has finished exporting.
- Phoenix comparison: verify patient, eye, acquisition time, examination number,
  all four applicable report views, labels, scales and numerical values. Record
  missing content; do not infer it from raw images.
- Backup/restore: pause writes; back up PostgreSQL and clinical files together;
  restore into a separate database/storage location and verify lookup, file
  hashes and selected report types. A backup is unvalidated until restored.
- Authentication: reject missing/invalid/expired dashboard tokens and invalid
  collector keys; test VNC authentication separately. Verify gateway isolation.
- Startup: reboot the test PC/server; verify Docker, VNC service, gateway and
  least-privilege collector startup, including an export created after reboot.
- Remote access: launch from the dashboard, enter credentials in noVNC, confirm
  the correct PC and examination, disconnect/reconnect and inspect audit events.

Only mark the first hospital machine integrated after all applicable checks pass.
