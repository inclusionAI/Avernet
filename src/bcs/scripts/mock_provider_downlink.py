#!/usr/bin/env python3
"""Lightweight mock HTTP Provider for BCS downlink testing.

The server intentionally uses only Python standard library modules so it can be
started on a developer machine without installing dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


JsonObject = dict[str, Any]


def now_ms() -> int:
    return int(time.time() * 1000)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def header_value(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is not None:
            return str(value)
    lower_name = name.lower()
    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == lower_name:
                return str(value)
    return None


def message_text(message: JsonObject | None) -> str:
    if not message:
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(str(part.get("text", "")))
            elif isinstance(part, str):
                texts.append(part)
        if texts:
            return "".join(texts)
        return compact_json(content)
    if content is not None:
        return compact_json(content)
    text = message.get("text")
    if text is not None:
        return str(text)
    return compact_json(message)


def normalize_incoming_message(request: JsonObject) -> JsonObject:
    message = request.get("message")
    if not isinstance(message, dict):
        message = {}
    timestamp = message.get("timestamp")
    if not isinstance(timestamp, int):
        timestamp = now_ms()
    return {
        "id": request.get("id", str(uuid.uuid4())),
        "role": message.get("role", "user"),
        "content": message_text(message),
        "timestamp": timestamp,
        "historyMeta": {
            "mockProvider": True,
            "method": request.get("method"),
        },
    }


class MockProviderState:
    def __init__(
        self,
        provider_id: str,
        final_text: str,
        auto_callback: bool = False,
        bcs_url: str = "http://127.0.0.1:21000",
        bot_runtime_token: str | None = None,
        bcs_to_provider_token: str | None = None,
        strict_auth: bool = False,
        callback_delay_ms: int = 50,
        verbose: bool = False,
    ) -> None:
        self.provider_id = provider_id
        self.final_text = final_text
        self.auto_callback = auto_callback
        self.bcs_url = bcs_url.rstrip("/")
        self.bot_runtime_token = bot_runtime_token
        self.bcs_to_provider_token = bcs_to_provider_token
        self.strict_auth = strict_auth
        self.callback_delay_ms = callback_delay_ms
        self.verbose = verbose
        self.sessions: dict[tuple[str, str], list[JsonObject]] = defaultdict(list)
        self.requests: list[JsonObject] = []
        self.callback_results: list[JsonObject] = []
        self.processed_ids: set[str] = set()
        self.aborted_sessions: set[tuple[str, str]] = set()
        self.lock = threading.RLock()

    def log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def reset(self) -> None:
        with self.lock:
            self.sessions.clear()
            self.requests.clear()
            self.callback_results.clear()
            self.processed_ids.clear()
            self.aborted_sessions.clear()

    def session_messages(self, provider_bot_ref: str, session_id: str) -> list[JsonObject]:
        with self.lock:
            return list(self.sessions[(provider_bot_ref, session_id)])

    def snapshot_requests(self) -> list[JsonObject]:
        with self.lock:
            return list(self.requests)

    def snapshot_sessions(self) -> JsonObject:
        with self.lock:
            return {
                f"{provider_bot_ref}::{session_id}": list(messages)
                for (provider_bot_ref, session_id), messages in self.sessions.items()
            }

    def handle_webhook(self, headers: Any, body: JsonObject) -> tuple[int, JsonObject]:
        auth_error = self.validate_auth(headers)
        if auth_error is not None:
            return auth_error

        provider_error = self.validate_provider(body)
        if provider_error is not None:
            return provider_error

        method = body.get("method")
        request_id = str(body.get("id", ""))
        with self.lock:
            duplicate = bool(request_id and request_id in self.processed_ids)
            if request_id:
                self.processed_ids.add(request_id)
            self.requests.append(
                {
                    "received_at": now_ms(),
                    "duplicate": duplicate,
                    "authorization": header_value(headers, "Authorization"),
                    "protocol_version": header_value(headers, "X-BCN-Protocol-Version"),
                    "body": body,
                }
            )

        if method == "chat.send":
            return self.handle_chat_send(body, duplicate)
        if method == "chat.inject":
            return self.handle_chat_inject(body, duplicate)
        if method == "chat.history":
            return self.handle_chat_history(body)
        if method == "chat.abort":
            return self.handle_chat_abort(body)
        return 400, {"ok": False, "error": "unsupported_method", "retryable": False}

    def validate_auth(self, headers: Any) -> tuple[int, JsonObject] | None:
        if not self.strict_auth:
            return None
        expected = self.bcs_to_provider_token
        if not expected:
            return None
        authorization = header_value(headers, "Authorization")
        if authorization != f"Bearer {expected}":
            return 401, {"ok": False, "error": "invalid_token", "retryable": False}
        return None

    def validate_provider(self, body: JsonObject) -> tuple[int, JsonObject] | None:
        to_bot = body.get("to_bot")
        if not isinstance(to_bot, dict):
            return 400, {"ok": False, "error": "missing_to_bot", "retryable": False}
        request_provider_id = to_bot.get("provider_id")
        if request_provider_id != self.provider_id:
            return 403, {
                "ok": False,
                "error": "provider_id_mismatch",
                "retryable": False,
            }
        return None

    def session_id(self, body: JsonObject) -> tuple[str, str]:
        to_bot = body.get("to_bot") or {}
        provider_bot_ref = str(to_bot.get("provider_bot_ref", ""))
        session_id = str(body.get("session_id") or body.get("session_key") or "")
        return provider_bot_ref, session_id

    def handle_chat_send(self, body: JsonObject, duplicate: bool) -> tuple[int, JsonObject]:
        provider_bot_ref, session_id = self.session_id(body)
        run_id = str(body.get("id", ""))
        if not duplicate:
            incoming = normalize_incoming_message(body)
            final = {
                "id": f"mock-final-{run_id or uuid.uuid4()}",
                "role": "assistant",
                "content": self.final_text,
                "timestamp": max(now_ms(), int(incoming.get("timestamp", 0)) + 1),
                "stopReason": "complete",
                "historyMeta": {
                    "mockProvider": True,
                    "run_id": run_id,
                },
            }
            with self.lock:
                self.sessions[(provider_bot_ref, session_id)].append(incoming)
                self.sessions[(provider_bot_ref, session_id)].append(final)
            self.schedule_callback(run_id)
        return 200, {"ok": True}

    def handle_chat_inject(self, body: JsonObject, duplicate: bool) -> tuple[int, JsonObject]:
        if not duplicate:
            provider_bot_ref, session_id = self.session_id(body)
            with self.lock:
                self.sessions[(provider_bot_ref, session_id)].append(
                    normalize_incoming_message(body)
                )
        return 200, {"ok": True}

    def handle_chat_history(self, body: JsonObject) -> tuple[int, JsonObject]:
        if body.get("before") is not None and body.get("after") is not None:
            return 400, {
                "ok": False,
                "error": "before_and_after_conflict",
                "retryable": False,
            }

        provider_bot_ref, session_id = self.session_id(body)
        limit = body.get("limit", 50)
        if not isinstance(limit, int) or limit <= 0:
            limit = 50
        limit = min(limit, 1000)
        before = body.get("before")
        after = body.get("after")

        messages = self.session_messages(provider_bot_ref, session_id)
        if isinstance(before, int):
            messages = [message for message in messages if message.get("timestamp", 0) < before]
        if isinstance(after, int):
            messages = [message for message in messages if message.get("timestamp", 0) > after]
        messages.sort(key=lambda message: int(message.get("timestamp", 0)), reverse=True)

        has_more = len(messages) > limit
        page = messages[:limit]
        response: JsonObject = {
            "ok": True,
            "session_id": session_id,
            "messages": page,
            "has_more": has_more,
        }
        if has_more and page:
            if isinstance(after, int):
                response["next_after"] = max(int(message["timestamp"]) for message in page)
            else:
                response["next_before"] = min(int(message["timestamp"]) for message in page)
        return 200, response

    def handle_chat_abort(self, body: JsonObject) -> tuple[int, JsonObject]:
        provider_bot_ref, session_id = self.session_id(body)
        with self.lock:
            self.aborted_sessions.add((provider_bot_ref, session_id))
        return 200, {"ok": True}

    def schedule_callback(self, run_id: str) -> None:
        if not self.auto_callback or not run_id:
            return
        delay = max(self.callback_delay_ms, 0) / 1000.0
        timer = threading.Timer(delay, self.post_bot_event, args=(run_id,))
        timer.daemon = True
        timer.start()

    def post_bot_event(self, run_id: str) -> None:
        if not self.bot_runtime_token:
            result = {
                "run_id": run_id,
                "ok": False,
                "error": "bot_runtime_token_not_configured",
            }
            with self.lock:
                self.callback_results.append(result)
            self.log(f"callback skipped: {compact_json(result)}")
            return

        url = f"{self.bcs_url}/bot/events"
        event_id = str(uuid.uuid4())
        payload = {
            "run_id": run_id,
            "seq": 1,
            "state": "final",
            "message": {
                "text": self.final_text,
            },
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.bot_runtime_token}",
                "Content-Type": "application/json",
                "X-BCN-Protocol-Version": "1.0",
                "X-BCN-Timestamp": str(now_ms()),
                "X-BCN-Provider-Id": self.provider_id,
                "X-BCN-Event-Id": event_id,
            },
        )
        result: JsonObject = {"run_id": run_id, "event_id": event_id, "url": url}
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result["status"] = response.status
                result["body"] = response.read().decode("utf-8", errors="replace")
                result["ok"] = 200 <= response.status < 300
        except urllib.error.HTTPError as error:
            result["status"] = error.code
            result["body"] = error.read().decode("utf-8", errors="replace")
            result["ok"] = False
        except Exception as error:  # pragma: no cover - depends on live BCS.
            result["ok"] = False
            result["error"] = str(error)

        with self.lock:
            self.callback_results.append(result)
        self.log(f"callback result: {compact_json(result)}")


class MockProviderHandler(BaseHTTPRequestHandler):
    server: "MockProviderServer"

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self.write_json(200, {"ok": True})
            return
        if path == "/requests":
            self.write_json(200, {"ok": True, "requests": self.server.state.snapshot_requests()})
            return
        if path == "/sessions":
            self.write_json(200, {"ok": True, "sessions": self.server.state.snapshot_sessions()})
            return
        if path == "/callbacks":
            with self.server.state.lock:
                callbacks = list(self.server.state.callback_results)
            self.write_json(200, {"ok": True, "callbacks": callbacks})
            return
        self.write_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/reset":
            self.server.state.reset()
            self.write_json(200, {"ok": True})
            return
        if path != "/webhook":
            self.write_json(404, {"ok": False, "error": "not_found"})
            return
        body = self.read_json_body()
        if body is None:
            self.write_json(400, {"ok": False, "error": "invalid_json", "retryable": False})
            return
        status, response = self.server.state.handle_webhook(self.headers, body)
        self.write_json(status, response)

    def read_json_body(self) -> JsonObject | None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        return value

    def write_json(self, status: int, body: JsonObject) -> None:
        data = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.server.state.verbose:
            super().log_message(fmt, *args)


class MockProviderServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], state: MockProviderState) -> None:
        super().__init__(address, MockProviderHandler)
        self.state = state


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a lightweight BCS Provider downlink webhook mock.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28080)
    parser.add_argument("--provider-id", default="provider-local")
    parser.add_argument("--bcs-url", default="http://127.0.0.1:21000")
    parser.add_argument("--bot-runtime-token")
    parser.add_argument("--bcs-to-provider-token")
    parser.add_argument("--strict-auth", action="store_true")
    parser.add_argument("--auto-callback", action="store_true")
    parser.add_argument("--callback-delay-ms", type=int, default=50)
    parser.add_argument("--final-text", default="mock provider final")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    state = MockProviderState(
        provider_id=args.provider_id,
        final_text=args.final_text,
        auto_callback=args.auto_callback,
        bcs_url=args.bcs_url,
        bot_runtime_token=args.bot_runtime_token,
        bcs_to_provider_token=args.bcs_to_provider_token,
        strict_auth=args.strict_auth,
        callback_delay_ms=args.callback_delay_ms,
        verbose=args.verbose,
    )
    server = MockProviderServer((args.host, args.port), state)
    host, port = server.server_address
    print("Mock Provider listening", flush=True)
    print(f"  webhook_url: http://{host}:{port}/webhook", flush=True)
    print(f"  health:      http://{host}:{port}/health", flush=True)
    print(f"  requests:    http://{host}:{port}/requests", flush=True)
    print(f"  sessions:    http://{host}:{port}/sessions", flush=True)
    print(f"  callbacks:   http://{host}:{port}/callbacks", flush=True)
    print(f"  provider_id: {args.provider_id}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping mock provider", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
