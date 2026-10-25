from __future__ import annotations

from io import BytesIO

import pytest

from app.vnc_gateway import GatewayConfigurationError, MachineVncTarget, authenticate_vnc, load_machine_target


class ScriptedSocket:
    def __init__(self, incoming: bytes):
        self.incoming = BytesIO(incoming)
        self.sent = bytearray()

    def recv(self, size: int) -> bytes:
        return self.incoming.read(size)

    def sendall(self, payload: bytes) -> None:
        self.sent.extend(payload)


def test_gateway_completes_classic_vnc_authentication_before_proxying():
    challenge = bytes.fromhex("00112233445566778899aabbccddeeff")
    upstream = ScriptedSocket(b"RFB 003.008\n" + b"\x01\x02" + challenge + b"\x00\x00\x00\x00")

    authenticate_vnc(upstream, "correct")

    assert upstream.sent[:13] == b"RFB 003.008\n\x02"
    assert upstream.sent[13:].hex() == "47b69870db5f914d6f11358d0ccb9db8"
    assert b"correct" not in upstream.sent


def test_gateway_fails_closed_for_unsupported_security_type():
    upstream = ScriptedSocket(b"RFB 003.008\n" + b"\x01\x13")
    with pytest.raises(GatewayConfigurationError, match="VNC authentication type 2"):
        authenticate_vnc(upstream, "secret")


def test_gateway_rejects_classic_vnc_passwords_that_would_be_silently_truncated():
    upstream = ScriptedSocket(b"RFB 003.008\n" + b"\x01\x02" + bytes(16))
    with pytest.raises(GatewayConfigurationError, match="1-to-8-byte"):
        authenticate_vnc(upstream, "ninebytes")


def test_machine_target_reads_password_from_separate_secret_file(tmp_path):
    secret = tmp_path / "vnc-password"
    secret.write_text("not-here\n", encoding="utf-8")
    config = tmp_path / "targets.json"
    config.write_text(
        '{"SIRIUS-1":{"host":"127.0.0.1","port":5900,"password_file":"' +
        str(secret).replace("\\", "\\\\") + '"}}',
        encoding="utf-8",
    )

    target = load_machine_target(config, "SIRIUS-1")

    assert target == MachineVncTarget(host="127.0.0.1", port=5900, password="not-here")
    assert "not-here" not in config.read_text(encoding="utf-8")
