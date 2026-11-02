#!/usr/bin/env python3
"""Local HTTP Provider and blocking Judge used by the BCS E2E coverage suite."""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class MockState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.provider_requests: list[dict[str, Any]] = []
        self.event_webhook_requests: list[dict[str, Any]] = []
        # Friend work-order surface: BCS re-addresses the forwarded principal
        # (iss=bcs) and posts TC friend grants to the bot-friend-auth endpoint;
        # the create/accept event notifications come in on the work-order path.
        self.friend_auth_sync_requests: list[dict[str, Any]] = []
        self.work_order_event_requests: list[dict[str, Any]] = []
        self.judge_started = threading.Event()
        self.judge_release = threading.Event()
        self.judge_outcomes: list[str] | None = None

    def reset(self) -> None:
        with self.lock:
            self.provider_requests.clear()
            self.event_webhook_requests.clear()
            self.friend_auth_sync_requests.clear()
            self.work_order_event_requests.clear()
            self.judge_outcomes = None
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
        if self.path == "/control/event-webhook/requests":
            with STATE.lock:
                requests = list(STATE.event_webhook_requests)
            self.send_json(200, {"requests": requests})
            return
        if self.path == "/control/friend-auth-sync/requests":
            with STATE.lock:
                requests = list(STATE.friend_auth_sync_requests)
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
        if self.path == "/control/event-webhook/clear":
            with STATE.lock:
                STATE.event_webhook_requests.clear()
            self.send_json(200, {"ok": True})
            return
        if self.path == "/control/friend-auth-sync/clear":
            with STATE.lock:
                STATE.friend_auth_sync_requests.clear()
            self.send_json(200, {"ok": True})
            return
        if self.path == "/control/judge/release":
            STATE.judge_release.set()
            self.send_json(200, {"ok": True})
            return
        if self.path == "/control/judge/outcomes":
            outcomes = self.read_json().get("outcomes")
            if not isinstance(outcomes, list) or not all(isinstance(item, str) and item for item in outcomes):
                self.send_json(400, {"error": "expected nonempty outcome strings"})
                return
            with STATE.lock:
                STATE.judge_outcomes = list(outcomes)
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
            if isinstance(body, dict) and body.get("method") == "caller.container.ensure":
                # Frozen wire subset (apps/bcs/docs caller-container-gateway.md):
                # ok + isolation.level=container_bound + a nonempty binding_ref +
                # a future container lifetime; BCS caps it against the trusted
                # deadline with min(), so any far-future value simply lets the
                # real deadline govern. No target/subject echo exists to
                # validate in this subset.
                far_future_ms = int(time.time() * 1000) + 7 * 24 * 60 * 60 * 1000
                self.send_json(200, {
                    "ok": True,
                    "isolation": {
                        "level": "container_bound",
                        "binding_ref": "e2e-" + str(body.get("id", "")),
                        "expires_at_ms": far_future_ms,
                    },
                })
                return
            self.send_json(200, {"ok": True})
            return
        if self.path == "/event-webhook":
            body = self.read_json()
            with STATE.lock:
                STATE.event_webhook_requests.append(
                    {
                        "authorization": self.headers.get("authorization"),
                        "content_type": self.headers.get("content-type"),
                        "body": body,
                    }
                )
            self.send_json(200, {"ok": True})
            return
        if self.path == "/api/v1/work-orders/events":
            # BCS friend-connect notification port: any 2xx means delivered.
            body = self.read_json()
            with STATE.lock:
                STATE.work_order_event_requests.append(
                    {
                        "principal": self.headers.get("x-avernet-principal"),
                        "body": body,
                    }
                )
            self.send_json(200, {"ok": True})
            return
        if self.path == "/api/internal/bot-friend-auth/sync":
            # The TC friend grant sync point: BCS arrives with the inbound
            # principal re-addressed as iss=bcs. BCS treats {"synced": true}
            # as confirmation anything else is reported as not applied.
            body = self.read_json()
            with STATE.lock:
                STATE.friend_auth_sync_requests.append(
                    {
                        "principal": self.headers.get("x-avernet-principal"),
                        "body": body,
                    }
                )
            self.send_json(200, {"synced": True, "reason": "created", "auth_id": 1})
            return
        if self.path == "/v1/chat/completions":
            self.read_json()
            STATE.judge_started.set()
            with STATE.lock:
                scripted = STATE.judge_outcomes is not None
                outcome = STATE.judge_outcomes.pop(0) if STATE.judge_outcomes else None
            if scripted and outcome is None:
                self.send_json(500, {"error": "unexpected_judge_invocation"})
                return
            if not scripted and not STATE.judge_release.wait(timeout=30):
                self.send_json(504, {"error": "judge_release_timeout"})
                return
            decision = {
                "outcome": outcome if scripted else "approved",
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
