#!/usr/bin/env python3
"""Local receiver for organization admin-run callbacks.

This utility intentionally uses only Python standard library modules.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit


JsonObject = dict[str, Any]


def now_ms() -> int:
    return int(time.time() * 1000)


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        str(name): "<redacted>"
        if str(name).lower() == "authorization"
        else str(value)
        for name, value in headers.items()
    }


class CallbackStore:
    """Thread-safe in-memory callback capture store."""

    def __init__(self) -> None:
        self._records: list[JsonObject] = []
        self._seen_counts: dict[str, int] = {}
        self._lock = threading.RLock()

    def record(
        self,
        headers: Mapping[str, str],
        body: JsonObject,
        method: str = "POST",
        path: str = "/callback",
    ) -> JsonObject:
        run_id_value = body.get("run_id")
        run_id = str(run_id_value) if run_id_value is not None else ""
        with self._lock:
            seen_count = self._seen_counts.get(run_id, 0) if run_id else 0
            if run_id:
                self._seen_counts[run_id] = seen_count + 1
            record: JsonObject = {
                "received_at": now_ms(),
                "method": method,
                "path": path,
                "headers": redact_headers(headers),
                "body": copy.deepcopy(body),
                "run_id": run_id,
                "duplicate": seen_count > 0,
            }
            self._records.append(record)
            return copy.deepcopy(record)

    def snapshot(self) -> JsonObject:
        with self._lock:
            return {
                "callbacks": copy.deepcopy(self._records),
                "duplicate_counts": {
                    run_id: count - 1
                    for run_id, count in self._seen_counts.items()
                    if count > 1
                },
            }

    def for_run(self, run_id: str) -> list[JsonObject]:
        with self._lock:
            return copy.deepcopy(
                [record for record in self._records if record["run_id"] == run_id]
            )

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._seen_counts.clear()


@dataclass(frozen=True)
class ServerConfig:
    response_status: int = 200
    response_delay_ms: int = 0
    expected_token: str | None = None
    expected_provider_id: str | None = None


class CallbackHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        config: ServerConfig,
        store: CallbackStore,
    ) -> None:
        self.config = config
        self.store = store
        super().__init__(server_address, CallbackRequestHandler)


class CallbackRequestHandler(BaseHTTPRequestHandler):
    server: CallbackHttpServer

    def write_json(self, status: int, body: JsonObject) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
        if path == "/health":
            snapshot = self.server.store.snapshot()
            self.write_json(
                200,
                {
                    "ok": True,
                    "callback_count": len(snapshot["callbacks"]),
                },
            )
            return
        if path == "/callbacks":
            self.write_json(200, self.server.store.snapshot())
            return
        prefix = "/callbacks/"
        if path.startswith(prefix):
            run_id = unquote(path[len(prefix) :])
            self.write_json(
                200,
                {
                    "callbacks": self.server.store.for_run(run_id),
                },
            )
            return
        self.write_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
        if path == "/reset":
            self.server.store.reset()
            self.write_json(200, {"ok": True, "callback_count": 0})
            return
        if path != "/callback":
            self.write_json(404, {"ok": False, "error": "not_found"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(content_length))
            if not isinstance(body, dict):
                raise ValueError("callback body must be a JSON object")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self.write_json(400, {"ok": False, "error": "invalid_json"})
            return
        record = self.server.store.record(
            dict(self.headers.items()),
            body,
            method="POST",
            path=path,
        )
        print(
            "Admin run callback received:\n"
            + json.dumps(record, ensure_ascii=False, indent=2),
            flush=True,
        )
        self.write_json(
            self.server.config.response_status,
            {"ok": True, "recorded": True},
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_server(
    host: str,
    port: int,
    config: ServerConfig,
    store: CallbackStore | None = None,
) -> CallbackHttpServer:
    return CallbackHttpServer(
        (host, port),
        config,
        store if store is not None else CallbackStore(),
    )
