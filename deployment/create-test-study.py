#!/usr/bin/env python3
"""Create one harmless text-only study for the two-laptop remote-access test."""

import argparse
import ipaddress
import json
from pathlib import Path
import secrets
import ssl
from urllib.parse import urlparse
from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler, Request


ROOT = Path(__file__).resolve().parents[1]


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_dashboard_url(raw: str) -> None:
    parsed = urlparse(raw)
    if not parsed.hostname:
        raise SystemExit("Dashboard URL must be absolute")
    if parsed.scheme == "https":
        return
    try:
        is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        is_loopback = parsed.hostname == "localhost"
    if parsed.scheme != "http" or not is_loopback:
        raise SystemExit("Dashboard URL must use HTTPS except for loopback development")


def field(boundary: str, name: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
        f"{value}\r\n"
    ).encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard-url", required=True)
    parser.add_argument("--machine-code", default="SIRIUS-TEST-01")
    parser.add_argument("--ca-file", type=Path, default=ROOT / "deployment" / "test-tls" / "ca.crt")
    parser.add_argument("--collector-config", type=Path, default=ROOT / "collector" / "config.json")
    args = parser.parse_args()
    validate_dashboard_url(args.dashboard_url)
    collector_config = json.loads(args.collector_config.read_text(encoding="utf-8-sig"))
    collector_key = collector_config.get("collector_key") if collector_config.get("machine_code") == args.machine_code else None
    if not collector_key:
        raise SystemExit("Collector config does not contain a credential for this machine")

    boundary = "hospital-test-" + secrets.token_hex(12)
    study_uid = f"REMOTE-ACCESS-TEST-{args.machine_code}"
    body = b"".join(
        [
            field(boundary, "machine_code", args.machine_code),
            field(boundary, "practo_uid", "REMOTE-ACCESS-TEST-PATIENT"),
            field(boundary, "study_uid", study_uid),
            field(boundary, "title", "Remote access test study - no patient data"),
            (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"remote-access-test.txt\"\r\n"
                "Content-Type: text/plain\r\n\r\nThis is synthetic test data only.\r\n"
            ).encode(),
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = Request(
        args.dashboard_url.rstrip("/") + "/api/collector/upload",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-Machine-Code": args.machine_code,
            "X-Collector-Key": collector_key,
        },
    )
    context = ssl.create_default_context(cafile=str(args.ca_file)) if request.full_url.startswith("https://") else None
    handlers = [NoRedirectHandler()]
    if context:
        handlers.append(HTTPSHandler(context=context))
    with build_opener(*handlers).open(request, timeout=30) as response:
        result = json.load(response)
    print(f"Created/reused study {result['study_uid']} with id {result['study_id']} for {args.machine_code}.")


if __name__ == "__main__":
    main()
