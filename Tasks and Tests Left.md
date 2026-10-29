| Task | What counts as done? |
|---|---|
| **Repository and build readiness** | Team identifies the exact shared commit and can reproduce the test build using documented prerequisites |
| **Two-laptop installation** | Preflight, installation, enrollment, and component status work on a controlled test PC |
| **Live remote session** | Authorized doctor opens a session; notification and End Session behavior are checked |
| **Network and recovery** | Disconnect/reconnect Wi-Fi, reboot the test PC, and restart components individually; record recovery and failures |
| **Data collection** | Test uploads and retry behavior with synthetic/de-identified data; verify queued data is retained |
| **Patient matching and reports** | Verify Practo UID mapping and report completeness against the source for each machine type |
| **Installer lifecycle** | Test upgrade, repair, rollback, and uninstall choices on a test PC; verify retained data and enrollment |
| **Security and release** | Verify certificate trust, approved execution under the target Windows policy, permissions, audit, signing, and hospital IT requirements |
| **Backup and restore** | Restore both database and clinical files into an isolated environment and check that records still point to the correct files |
