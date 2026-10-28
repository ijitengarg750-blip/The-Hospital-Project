from __future__ import annotations

import base64
from hashlib import sha256
from io import BytesIO
import ssl
import time

import pytest

from app.config import Settings
from app.models import Machine
from app.vnc_gateway import GatewayConfigurationError, MachineVncTarget, authenticate_vnc, load_machine_target, mint_tunnel_ticket, open_secure_transport, probe_secure_transport, validate_tunnel_ticket


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


def test_secure_transport_fails_closed_without_both_peer_certificates(tmp_path):
    machine = Machine(code="SIRIUS-1", name="Sirius")
    network = type("Network", (), {
        "host": "127.0.0.1",
        "port": 5910,
        "tls_server_name": "SIRIUS-1",
        "agent_certificate_pem": None,
    })()

    with pytest.raises(GatewayConfigurationError, match="Mutual TLS"):
        open_secure_transport(machine, network, Settings(), "session-1", "ticket")


def test_tunnel_ticket_is_short_lived_session_specific_and_contains_no_agent_key():
    key_hash = sha256(b"agent-secret").hexdigest()
    ticket = mint_tunnel_ticket("session-1", key_hash)

    encoded_payload, encoded_mac = ticket.split(".")
    payload_bytes = base64.urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4))
    payload = payload_bytes.decode()
    session_id, expires, nonce = payload.split("\n")
    assert session_id == "session-1"
    assert 0 < int(expires) - int(time.time()) <= 30
    assert nonce
    assert encoded_mac
    assert "agent-secret" not in ticket
    assert validate_tunnel_ticket("session-1", ticket, key_hash)
    assert not validate_tunnel_ticket("session-2", ticket, key_hash)
    assert not validate_tunnel_ticket("session-1", ticket + "x", key_hash)


def test_secure_transport_sends_session_ticket_before_accepting_ready(monkeypatch):
    channel = ScriptedSocket(b"HOSPITAL-RFB/2\n")
    channel.settimeout = lambda _: None
    channel.close = lambda: None
    monkeypatch.setattr("app.vnc_gateway._open_secure_channel", lambda *_: channel)

    connection = open_secure_transport(object(), object(), object(), "session-1", "signed-ticket")

    assert connection is channel
    assert channel.sent == b'{"session_id":"session-1","ticket":"signed-ticket"}\n'


@pytest.mark.parametrize(
    ("cause", "expected"),
    [
        (ConnectionRefusedError(), "local bind health"),
        (TimeoutError(), "firewall filtering"),
        (ssl.SSLCertVerificationError(), "certificate validation"),
    ],
)
def test_transport_probe_distinguishes_listener_network_and_tls_failures(monkeypatch, cause, expected):
    def fail(*_):
        raise GatewayConfigurationError("failed") from cause

    monkeypatch.setattr("app.vnc_gateway._open_secure_channel", fail)

    status, latency, error = probe_secure_transport(object(), object(), object())

    assert status == "unreachable"
    assert latency is None
    assert expected in error
