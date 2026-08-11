#!/usr/bin/env python3
"""Deterministic OpenAI-compatible HTTP model used by OpenClaw compat tests."""

from __future__ import annotations

import argparse
import json
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class RequestLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []

    def append(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.requests.append(entry)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.requests)


class CompatLlmHandler(BaseHTTPRequestHandler):
    server_version = "AvernetOpenClawCompatLlm/1.0"

    @property
    def response_text(self) -> str:
        return self.server.response_text  # type: ignore[attr-defined]

    @property
    def request_log(self) -> RequestLog:
        return self.server.request_log  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        print(format % args, flush=True)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("request JSON must be an object")
        return parsed

    def send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlsplit(self.path).path
        if path == "/health":
            self.send_json(200, {"ok": True})
            return
        if path == "/v1/models":
            self.send_json(
                200,
                {
                    "object": "list",
                    "data": [{"id": "compat-model", "object": "model", "owned_by": "avernet"}],
                },
            )
            return
        if path == "/control/requests":
            self.send_json(200, {"requests": self.request_log.snapshot()})
            return
        self.send_json(404, {"error": {"message": "not_found"}})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlsplit(self.path).path
        if path != "/v1/chat/completions":
            self.send_json(404, {"error": {"message": "not_found"}})
            return
        try:
            body = self.read_json()
        except (json.JSONDecodeError, ValueError) as error:
            self.send_json(400, {"error": {"message": str(error)}})
            return

        self.request_log.append(
            {
                "path": path,
                "model": body.get("model"),
                "stream": bool(body.get("stream")),
                "message_count": len(body.get("messages", [])) if isinstance(body.get("messages"), list) else 0,
                "tool_count": len(body.get("tools", [])) if isinstance(body.get("tools"), list) else 0,
            }
        )
        if body.get("stream"):
            self.send_streaming_completion(str(body.get("model") or "compat-model"))
        else:
            self.send_json(
                200,
                {
                    "id": "chatcmpl-avernet-compat",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": body.get("model") or "compat-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": self.response_text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )

    def send_streaming_completion(self, model: str) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        chunks = [
            {"role": "assistant", "content": ""},
            {"content": self.response_text},
        ]
        for delta in chunks:
            payload = {
                "id": "chatcmpl-avernet-compat",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode())
            self.wfile.flush()
        final_payload = {
            "id": "chatcmpl-avernet-compat",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        self.wfile.write(f"data: {json.dumps(final_payload, separators=(',', ':'))}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--requests-file", type=Path, required=True)
    parser.add_argument("--response-text", default="OPENCLAW_COMPAT_OK")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    request_log = RequestLog(args.requests_file)
    server = ThreadingHTTPServer((args.host, args.port), CompatLlmHandler)
    server.request_log = request_log  # type: ignore[attr-defined]
    server.response_text = args.response_text  # type: ignore[attr-defined]
    host, port = server.server_address[:2]
    args.ready_file.parent.mkdir(parents=True, exist_ok=True)
    args.ready_file.write_text(
        json.dumps({"base_url": f"http://{host}:{port}", "model": "compat-model"}) + "\n",
        encoding="utf-8",
    )

    def stop_server(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    main()
