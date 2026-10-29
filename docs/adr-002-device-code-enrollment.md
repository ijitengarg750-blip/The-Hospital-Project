# ADR-002: approve enrollment with a device verification code

Date: 2026-10-29

## Decision

Diagnostic-PC enrollment uses a short-lived device-pairing flow. The installer creates an ephemeral RSA key pair and sends only the public key when it claims an administrator-opened enrollment window. The Diagnostic Machines page shows a six-digit verification code and requires an administrator to approve the matching code visible on the PC. The backend returns the agent credential only to that claim, encrypted with AES-GCM; the AES key is encrypted to the installer's RSA public key.

The browser receives the verification code and component status, but never receives the claim identifier, agent key, collector key, private key, or legacy enrollment bearer token. Lost completion responses are safe to retry because the backend returns the same encrypted payload for the same completed claim. Claims expire with the ten-minute invitation and are limited to five attempts per invitation.

## Why

The earlier administrator endpoint returned a bearer enrollment token to browser code so it could be copied to the PC. Phase 3 requires a guided browser workflow without exposing enrollment secrets in browser responses. Device-code confirmation keeps the human verification step while ensuring only the installer-held private key can recover the credential.

## Consequences

- Hospital IT must compare the code on the diagnostic PC with the code in the administrator page before approval.
- The existing signed pilot installer now waits for approval instead of prompting for a copied token.
- Collector credential rotation accepts an installer public key and returns ciphertext rather than a plaintext credential.
- The existing `/api/agent/enroll` token route remains for compatibility, but the administrator invitation endpoint no longer returns a token; new installations use claims.
- The one-backend-process limitation from Phase 2 remains unchanged.

## Rejected alternatives

- Displaying a one-time bearer token in the browser: violates the browser-secret boundary and is easy to copy into an unintended channel.
- Enrolling during any open time window using only a machine code: permits unauthorized enrollment by anyone who knows the code.
- Adding an enrollment broker service or port: unnecessary; the existing authenticated backend and outbound HTTPS path are sufficient.
