# ADR-001: mandatory encrypted VNC transport

Date: 2026-10-25

## Decision

Remote framebuffer traffic uses a mutually authenticated TLS tunnel between the dashboard gateway and a Windows machine agent. The agent authenticates to the local, loopback VNC server and proxies the already-authorized RFB stream. No browser or backend route receives the reusable VNC password or agent private key.

The gateway trusts the per-machine public certificate enrolled with a single-use, 10-minute token. The agent trusts a dedicated gateway client CA received during enrollment. Both sides require TLS 1.2 or newer. Missing credentials, hostname/certificate failure, a bad tunnel preface, or an unavailable agent fails closed without a plaintext fallback.

## Why

Classic RFB authentication does not encrypt desktop traffic. Host firewall isolation alone protects routing but not confidentiality on an allowed path. Placing the encryption endpoint on the diagnostic PC preserves compatibility with a loopback-only legacy VNC server while keeping the doctor browser credential-free.

## Consequences

- Hospital IT must allow the gateway to agent TCP port (default 5910) and keep VNC TCP 5900 local-only.
- Enrollment and certificate rotation are operational requirements.
- Windows 7/8/8.1 artifacts remain an exception path built with Go 1.20 and require physical/VM verification; current Windows uses the supported toolchain.
- The project does not provision a VPN. Existing hospital VPN/VLAN controls may be layered around this channel.
- The old JSON/password-file direct gateway remains test/legacy code only and is not reachable from the production WebSocket route.

## Rejected alternatives

- Plain RFB over the LAN: desktop contents remain readable in transit.
- Passing the VNC password to noVNC: exposes a reusable machine credential to browser code.
- Opportunistic encryption with plaintext fallback: configuration errors silently remove the core security guarantee.
- Automatic VPN deployment: outside the application boundary and hospital network-change authority.
