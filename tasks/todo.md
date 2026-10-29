# Phase 2 implementation

- [x] Slice 1: authenticated outbound control connection and session dispatch
- [x] Slice 2: session-specific outbound data stream and browser relay
- [x] Slice 3: reconnecting Windows connector and independent recovery
- [x] Slice 4: restart reconciliation, stale cleanup, certificate rotation, and operational docs
- [x] Full backend and Go suites
- [x] Adversarial security review

## Production gates — do not mark complete from automated tests

- [ ] Production credential cutover
- [ ] Physical two-laptop end-to-end test
- [ ] Real supported Windows versions and architectures
- [ ] Independent service/component crash recovery
- [ ] DHCP address changes without manual server updates
- [ ] Wi-Fi outage and reconnection
- [ ] Offline local End Session and later server reconciliation
- [ ] Server restart reconciliation
- [ ] Certificate rotation with overlap and revocation
- [ ] WDAC authorization of final signed binaries
- [ ] Re-sign every changed deployment script
- [ ] Confirm no inbound TCP 5910 firewall rule in outbound production mode
- [ ] Confirm TightVNC remains bound to loopback
- [ ] Confirm exactly one backend process/replica, or implement shared connection and replay coordination before scaling
