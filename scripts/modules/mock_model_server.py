#!/usr/bin/env python3
"""Minimal non-streaming OpenAI-compatible model server for local singlebox."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


SENDER_METADATA_PATTERN = re.compile(
    r"Sender \(untrusted metadata\):\s*```json\s*"
)


class MockModelError(ValueError):
    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def message_texts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    parts: list[str] = []
    for item in content:
        if (
            isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ):
            parts.append(item["text"])
    return parts


def sender_name(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise MockModelError("missing_sender")

    latest_user_message = next(
        (
            message
            for message in reversed(messages)
            if isinstance(message, dict) and message.get("role") == "user"
        ),
        None,
    )
    if latest_user_message is None:
        raise MockModelError("missing_sender")

    text = "\n".join(message_texts(latest_user_message.get("content")))
    marker = SENDER_METADATA_PATTERN.search(text)
    if marker is None:
        raise MockModelError("missing_sender")
    block_end = text.find("```", marker.end())
    if block_end < 0:
        raise MockModelError("missing_sender")
    try:
        metadata = json.loads(text[marker.end() : block_end])
    except json.JSONDecodeError as error:
        raise MockModelError("missing_sender") from error
    if not isinstance(metadata, dict):
        raise MockModelError("missing_sender")

    sender = metadata.get("label")
    if (
        not isinstance(sender, str)
        or not sender.strip()
        or len(sender) > 128
        or "\n" in sender
        or "\r" in sender
    ):
        raise MockModelError("missing_sender")
    return sender.strip()


def error_response(error: str, now: datetime | None = None) -> dict[str, str]:
    return {"error": error, "timestamp": format_timestamp(now or utc_now())}


def completion_response(
    payload: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    sender = sender_name(payload)
    response_time = now or utc_now()
    timestamp = format_timestamp(response_time)
    return {
        "id": "mock-sender-reply",
        "object": "chat.completion",
        "created": int(response_time.timestamp()),
        "model": payload.get("model", "singlebox-mock"),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": (
                        "[from OpenAI-compatible Mock Model Server]: "
                        f"Hi, {sender}, now time is {timestamp}"
                    ),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "SingleboxMockModel/1.0"

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {"status": "ok", "service": "singlebox-mock-model"})
            return
        self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_json(404, {"error": "not_found"})
            return
        try:
            payload = self.read_json()
        except (json.JSONDecodeError, ValueError):
            self.send_json(400, {"error": "invalid_json"})
            return
        try:
            response = completion_response(payload)
        except MockModelError as error:
            self.send_json(400, error_response(error.error))
            return
        self.send_json(200, response)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
