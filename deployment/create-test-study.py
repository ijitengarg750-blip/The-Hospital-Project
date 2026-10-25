#!/usr/bin/env python3
"""Create one harmless text-only study for the two-laptop remote-access test."""

import argparse
import json
from pathlib import Path
import secrets
import ssl
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        if raw.strip() and not raw.lstrip().startswith("#") and "=" in raw:
            key, value = raw.split("=", 1)
            values[key.strip()] = value.strip()
    return values


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
    args = parser.parse_args()
    collector_key = load_env().get("COLLECTOR_API_KEY")
    if not collector_key:
        raise SystemExit("COLLECTOR_API_KEY is missing from .env")

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
            "X-Collector-Key": collector_key,
        },
    )
    context = ssl.create_default_context(cafile=str(args.ca_file)) if request.full_url.startswith("https://") else None
    with urlopen(request, context=context, timeout=30) as response:
        result = json.load(response)
    print(f"Created/reused study {result['study_uid']} with id {result['study_id']} for {args.machine_code}.")


if __name__ == "__main__":
    main()
