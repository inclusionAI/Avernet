#!/usr/bin/env python3
"""Minimal non-streaming OpenAI-compatible model server for local singlebox."""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


SENDER_METADATA_PATTERN = re.compile(
    r"Sender \(untrusted metadata\):\s*```json\s*"
)
LOGGER = logging.getLogger(__name__)


def fallback_sender(reason: str, content_type: str = "absent") -> str:
    # Metadata is optional display text, never authentication. Do not log the
    # prompt, sender label, attachment URLs or malformed metadata itself.
    LOGGER.warning(
        "mock_sender_fallback reason=%s content_type=%s", reason, content_type
    )
    return "user"


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
    if not isinstance(messages, list) or not messages:
        raise MockModelError("invalid_messages")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in (
            "system", "developer", "user", "assistant", "tool", "function"
        ):
            raise MockModelError("invalid_messages")
        content = message.get("content")
        if not isinstance(content, (str, list)) and not (
            content is None and message["role"] == "assistant"
            and (message.get("tool_calls") or message.get("function_call"))
        ):
            raise MockModelError("invalid_messages")
        if isinstance(content, list) and any(
            not isinstance(part, dict)
            or not isinstance(part.get("type"), str)
            or (part["type"] == "text" and not isinstance(part.get("text"), str))
            for part in content
        ):
            raise MockModelError("invalid_messages")

    latest_user_message = next(
        (
            message
            for message in reversed(messages)
            if isinstance(message, dict) and message.get("role") == "user"
        ),
        None,
    )
    if latest_user_message is None:
        return fallback_sender("no_user_message")

    content_type = type(latest_user_message.get("content")).__name__
    text = "\n".join(message_texts(latest_user_message.get("content")))
    marker = SENDER_METADATA_PATTERN.search(text)
    if marker is None:
        return fallback_sender("no_sender_block", content_type)
    block_end = text.find("```", marker.end())
    if block_end < 0:
        return fallback_sender("unterminated_sender_block", content_type)
    try:
        metadata = json.loads(text[marker.end() : block_end])
    except json.JSONDecodeError:
        return fallback_sender("malformed_sender_json", content_type)
    if not isinstance(metadata, dict):
        return fallback_sender("invalid_sender_object", content_type)

    sender = metadata.get("label")
    if (
        not isinstance(sender, str)
        or not sender.strip()
        or len(sender) > 128
        or "\n" in sender
        or "\r" in sender
    ):
        return fallback_sender("invalid_sender_label", content_type)
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
    response_delay_seconds = 0.0

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
        # Delay only valid completions, never health checks or error responses.
        # ThreadingHTTPServer keeps different Bot requests independent.
        if self.response_delay_seconds > 0:
            time.sleep(self.response_delay_seconds)
        self.send_json(200, response)


def nonnegative_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("delay must be finite and non-negative")
    return seconds


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument(
        "--response-delay-seconds", type=nonnegative_seconds, default=0.0,
        help="Delay valid completion responses for queue tests (default: 0).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    Handler.response_delay_seconds = args.response_delay_seconds
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
