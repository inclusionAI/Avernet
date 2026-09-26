#!/usr/bin/env python3
"""Dependency-free credentials and WebSocket probe for the BCS E2E suite."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import socket
import struct
import sys
import time
from urllib.parse import urlsplit


_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _base64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _gateway_principal_token(
    *, user_id: str, username: str, tenant: str, signing_key: str
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "kid": "bare", "typ": "JWT"}
    claims = {
        "iss": "gateway",
        "aud": "bcs",
        "iat": now,
        "exp": now + 60,
        "principals": [
            {
                "type": "user",
                "tenant": tenant,
                "subject": {
                    "id": user_id,
                    "username": username,
                    "display_name": None,
                    "full_name": None,
                    "tenant_id": tenant,
                },
            }
        ],
    }
    encoded_header = _base64url(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    )
    encoded_claims = _base64url(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    )
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = hmac.new(signing_key.encode(), signing_input, hashlib.sha256).digest()
    return f"{signing_input.decode('ascii')}.{_base64url(signature)}"


def _read_upgrade_response(connection: socket.socket) -> bytes:
    response = bytearray()
    while b"\r\n\r\n" not in response:
        chunk = connection.recv(4096)
        if not chunk:
            break
        response.extend(chunk)
        if len(response) > 16_384:
            raise RuntimeError("WebSocket Upgrade headers are too large")
    return bytes(response)


def _parse_upgrade_headers(response: bytes) -> tuple[str, dict[str, str]]:
    header_block, separator, _ = response.partition(b"\r\n\r\n")
    if not separator:
        raise RuntimeError("WebSocket Upgrade response is incomplete")
    lines = header_block.decode("iso-8859-1").split("\r\n")
    status_line = lines[0]
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, delimiter, value = line.partition(":")
        if not delimiter:
            raise RuntimeError("WebSocket Upgrade response contains an invalid header")
        headers[name.strip().lower()] = value.strip()
    return status_line, headers


def _masked_close_frame() -> bytes:
    payload = struct.pack("!H", 1000)
    mask = os.urandom(4)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return b"\x88" + bytes([0x80 | len(payload)]) + mask + masked


def _probe_websocket(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "ws" or not parsed.hostname or parsed.username is not None:
        raise RuntimeError("WebSocket probe requires a plain ws:// URL without userinfo")
    port = parsed.port or 80
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    host = parsed.hostname
    host_header = host if port == 80 else f"{host}:{port}"
    websocket_key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {target} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {websocket_key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).encode("ascii")

    with socket.create_connection((host, port), timeout=5) as connection:
        connection.settimeout(5)
        connection.sendall(request)
        status_line, headers = _parse_upgrade_headers(_read_upgrade_response(connection))
        if status_line != "HTTP/1.1 101 Switching Protocols":
            raise RuntimeError(f"WebSocket Upgrade returned {status_line!r}")
        expected_accept = base64.b64encode(
            hashlib.sha1(f"{websocket_key}{_WEBSOCKET_GUID}".encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("upgrade", "").lower() != "websocket":
            raise RuntimeError("WebSocket Upgrade response omitted the Upgrade header")
        if "upgrade" not in headers.get("connection", "").lower():
            raise RuntimeError("WebSocket Upgrade response omitted the Connection header")
        if not hmac.compare_digest(headers.get("sec-websocket-accept", ""), expected_accept):
            raise RuntimeError("WebSocket Upgrade response has an invalid accept value")
        connection.sendall(_masked_close_frame())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    principal = commands.add_parser("principal")
    principal.add_argument("--user-id", required=True)
    principal.add_argument("--username", required=True)
    principal.add_argument("--tenant", required=True)
    principal.add_argument("--signing-key", required=True)

    websocket = commands.add_parser("websocket")
    websocket.add_argument("--url", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "principal":
            print(
                _gateway_principal_token(
                    user_id=args.user_id,
                    username=args.username,
                    tenant=args.tenant,
                    signing_key=args.signing_key,
                )
            )
        else:
            _probe_websocket(args.url)
    except Exception as error:
        print(f"group-session WebSocket probe failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
