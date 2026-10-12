#!/usr/bin/env python3
"""Bot-local Relay HTTP transport; never prints or accepts a session credential.

Usage: relay_http.py GET /api/v1/collaboration/tasks/... --backend-url <origin> --relay-token <generation>
       relay_http.py POST /api/v1/collaboration/tasks/... --backend-url <origin> < body.json

The Backend origin comes from the server-generated execution instruction and
Bot identity comes from runtime-owned state. The JSON body cannot choose either.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def _backend_origin(configured_url: str) -> str:
    origin = configured_url.rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path:
        raise ValueError("backend URL must be a configured origin")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("Relay Backend must use HTTPS outside localhost")
    return origin


def _bot_token() -> str:
    data_dir = os.environ.get("BOT_DATA_DIR", "")
    if not data_dir:
        raise ValueError("BOT_DATA_DIR is not set")
    session_path = Path(data_dir) / ".bcs" / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    token = str(session.get("token") or "")
    if not token:
        raise ValueError("Bot BCS session token is unavailable")
    return token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call the authenticated Relay task HTTP interface")
    parser.add_argument("method", choices=["GET", "POST"])
    parser.add_argument("path")
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--relay-token", default="")
    args = parser.parse_args(argv)
    if not args.path.startswith("/api/v1/collaboration/tasks/") or "//" in args.path:
        parser.error("path must be a task API path, not a URL")
    if "?" in args.path or "#" in args.path:
        parser.error("query and fragment are not supported")
    try:
        origin = _backend_origin(args.backend_url)
        token = _bot_token()
        body = sys.stdin.buffer.read() if args.method == "POST" else None
        headers = {"Authorization": f"Bearer {token}"}
        if args.relay_token:
            headers["X-Relay-Token"] = args.relay_token
        if body is not None:
            headers["Content-Type"] = "application/json"
            json.loads(body)
        request = Request(origin + args.path, data=body, headers=headers, method=args.method)
        with urlopen(request, timeout=30) as response:
            sys.stdout.buffer.write(response.read())
            sys.stdout.buffer.write(b"\n")
        return 0
    except HTTPError as exc:
        sys.stderr.write(f"Relay HTTP {exc.code}: {exc.reason}\n")
        return 1
    except (OSError, ValueError, URLError) as exc:
        sys.stderr.write(f"Relay HTTP unavailable: {type(exc).__name__}: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
