"""Local test agent for the remote-session control plane; not for production."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def api(server: str, path: str, machine_code: str, agent_key: str, method: str = "GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(
        f"{server.rstrip('/')}{path}",
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Machine-Code": machine_code,
            "X-Agent-Key": agent_key,
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            content = response.read()
            return json.loads(content) if content else None
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail", "request rejected")
        except (json.JSONDecodeError, AttributeError):
            detail = "request rejected"
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError("hospital server is unavailable") from exc


def parse_args():
    parser = argparse.ArgumentParser(description="Simulate one diagnostic-PC session agent")
    parser.add_argument("--server", default="http://localhost:8080")
    parser.add_argument("--machine-code", required=True)
    parser.add_argument(
        "--state",
        choices=("unlocked", "locked", "signed_out", "booting", "busy", "unavailable"),
        default="unlocked",
    )
    parser.add_argument("--end-session", metavar="SESSION_ID")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    agent_key = os.environ.get("HOSPITAL_AGENT_KEY", "")
    if not agent_key:
        print("Set HOSPITAL_AGENT_KEY to the one-time provisioned machine credential.", file=sys.stderr)
        return 2

    if args.end_session:
        ended = api(
            args.server,
            f"/api/agent/remote-sessions/{args.end_session}/end",
            args.machine_code,
            agent_key,
            method="POST",
        )
        print(f"Ended session {ended['id']} locally.")
        return 0

    print(f"Simulated agent online for {args.machine_code}; state={args.state}.")
    print("Use a second invocation with --end-session SESSION_ID for local termination.")
    last_session_status: tuple[str, str] | None = None
    while True:
        try:
            api(
                args.server,
                "/api/agent/heartbeat",
                args.machine_code,
                agent_key,
                method="POST",
                body={"state": args.state},
            )
            session = api(args.server, "/api/agent/remote-sessions/next", args.machine_code, agent_key)
            if session and session["status"] == "requested":
                session = api(
                    args.server,
                    f"/api/agent/remote-sessions/{session['id']}/ready",
                    args.machine_code,
                    agent_key,
                    method="POST",
                )
            current = (session["id"], session["status"]) if session else None
            if current != last_session_status:
                if session:
                    print(f"Session {session['id']} from {session['doctor_username']}: {session['status']}")
                elif last_session_status:
                    print("No active remote session.")
                last_session_status = current
        except RuntimeError as exc:
            print(f"Agent warning: {exc}", file=sys.stderr)
        time.sleep(2)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nSimulated agent stopped.")

