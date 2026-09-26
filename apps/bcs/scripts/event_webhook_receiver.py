#!/usr/bin/env python3
"""Minimal local receiver for BCS Event Webhooks.

The receiver prints each POST request as one JSON log record and always
acknowledges it with HTTP 204. It is intended only for local manual testing.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class EventWebhookServer(ThreadingHTTPServer):
    daemon_threads = True


class EventWebhookHandler(BaseHTTPRequestHandler):
    server: EventWebhookServer

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._read_body()
        record: dict[str, Any] = {
            "received_at": datetime.now(UTC).isoformat(),
            "remote_address": self.client_address[0],
            "method": "POST",
            "path": self.path,
            "body": body.decode("utf-8", errors="replace"),
        }
        print(json.dumps(record, ensure_ascii=False), flush=True)
        self.send_response(204)
        self.end_headers()

    def _read_body(self) -> bytes:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        return self.rfile.read(max(content_length, 0))

    def log_message(self, format: str, *args: Any) -> None:
        return


def bounded_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print BCS Event Webhooks and always return HTTP 204.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=bounded_port, default=28082)
    return parser.parse_args(argv)


def create_server(host: str, port: int) -> EventWebhookServer:
    return EventWebhookServer((host, port), EventWebhookHandler)


def main() -> int:
    args = parse_args()
    server = create_server(args.host, args.port)
    host, port = server.server_address
    print(
        f"BCS Event Webhook receiver listening on http://{host}:{port}/events",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBCS Event Webhook receiver stopped.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
