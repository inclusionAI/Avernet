#!/usr/bin/env python3
"""Local HTTP Provider and blocking Judge used by the BCS E2E coverage suite."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class MockState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.provider_requests: list[dict[str, Any]] = []
        self.judge_started = threading.Event()
        self.judge_release = threading.Event()

    def reset(self) -> None:
        with self.lock:
            self.provider_requests.clear()
        self.judge_started.clear()
        self.judge_release.clear()


STATE = MockState()


class Handler(BaseHTTPRequestHandler):
    server_version = "BcsE2eHttpMock/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(format % args, flush=True)

    def read_json(self) -> Any:
        length = int(self.headers.get("content-length", "0"))
        payload = self.rfile.read(length) if length else b"{}"
        return json.loads(payload)

    def send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path == "/health":
            self.send_json(200, {"ok": True})
            return
        if self.path == "/control/provider/requests":
            with STATE.lock:
                requests = list(STATE.provider_requests)
            self.send_json(200, {"requests": requests})
            return
        if self.path == "/control/judge/status":
            self.send_json(
                200,
                {
                    "started": STATE.judge_started.is_set(),
                    "released": STATE.judge_release.is_set(),
                },
            )
            return
        self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path == "/control/reset":
            STATE.reset()
            self.send_json(200, {"ok": True})
            return
        if self.path == "/control/provider/clear":
            with STATE.lock:
                STATE.provider_requests.clear()
            self.send_json(200, {"ok": True})
            return
        if self.path == "/control/judge/release":
            STATE.judge_release.set()
            self.send_json(200, {"ok": True})
            return
        if self.path == "/provider/webhook":
            body = self.read_json()
            with STATE.lock:
                STATE.provider_requests.append(
                    {
                        "authorization": self.headers.get("authorization"),
                        "body": body,
                    }
                )
            self.send_json(200, {"ok": True})
            return
        if self.path == "/v1/chat/completions":
            self.read_json()
            STATE.judge_started.set()
            if not STATE.judge_release.wait(timeout=30):
                self.send_json(504, {"error": "judge_release_timeout"})
                return
            decision = {
                "outcome": "approved",
                "reason": "candidate satisfies the E2E criteria",
                "confidence": 0.99,
                "checked_criteria": [],
                "retry_instruction": "",
            }
            self.send_json(
                200,
                {
                    "choices": [
                        {"message": {"content": json.dumps(decision)}}
                    ]
                },
            )
            return
        self.send_json(404, {"error": "not_found"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready-file", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    host, port = server.server_address[:2]
    ready_file = Path(args.ready_file)
    ready_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file.write_text(f"http://{host}:{port}\n", encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
