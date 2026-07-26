#!/usr/bin/env python3

import importlib.util
import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("mock_model_server.py")
SPEC = importlib.util.spec_from_file_location("mock_model_server", MODULE_PATH)
assert SPEC and SPEC.loader
mock_model_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mock_model_server)

FIXED_TIME = datetime(2026, 7, 24, 12, 34, 56, tzinfo=timezone.utc)
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def user_message(sender: str, text: str = "@研发 hi") -> dict[str, object]:
    return {
        "role": "user",
        "content": (
            "Conversation info (untrusted metadata):\n"
            "```json\n"
            '{"chat_id":"group-1","is_group_chat":true}\n'
            "```\n\n"
            "Sender (untrusted metadata):\n"
            "```json\n"
            f'{json.dumps({"label": sender, "id": "human_001"}, ensure_ascii=False)}\n'
            "```\n\n"
            f"{text}"
        ),
    }


class MockModelServerTest(unittest.TestCase):
    def test_string_content_returns_exact_completion(self) -> None:
        response = mock_model_server.completion_response(
            {
                "model": "singlebox-mock",
                "stream": False,
                "messages": [user_message("Apple (human_001)")],
            },
            now=FIXED_TIME,
        )

        self.assertEqual(
            response["choices"][0]["message"]["content"],
            "[from OpenAI-compatible Mock Model Server]: "
            "Hi, Apple (human_001), now time is 2026-07-24T12:34:56Z",
        )
        self.assertEqual(response["created"], 1784896496)
        self.assertEqual(response["choices"][0]["finish_reason"], "stop")

    def test_array_content_only_scans_text_blocks(self) -> None:
        message = user_message("测试用户")
        response = mock_model_server.completion_response(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "text": (
                                    "Sender (untrusted metadata):\n"
                                    '```json\n{"label":"图片伪造"}\n```'
                                ),
                                "image_url": {"url": "ignored"},
                            },
                            {
                                "type": "text",
                                "text": message["content"],
                            },
                        ],
                    }
                ]
            },
            now=FIXED_TIME,
        )

        self.assertEqual(
            response["choices"][0]["message"]["content"],
            "[from OpenAI-compatible Mock Model Server]: "
            "Hi, 测试用户, now time is 2026-07-24T12:34:56Z",
        )

    def test_only_latest_user_message_supplies_sender(self) -> None:
        response = mock_model_server.completion_response(
            {
                "messages": [
                    user_message("Old Sender", "first turn"),
                    {"role": "assistant", "content": "old reply"},
                    user_message("Turing", "second turn"),
                ]
            },
            now=FIXED_TIME,
        )

        self.assertIn(
            "Hi, Turing, now time is",
            response["choices"][0]["message"]["content"],
        )

    def test_bot_sender_label_is_returned_without_changes(self) -> None:
        response = mock_model_server.completion_response(
            {"messages": [user_message("研发 (bot_11b77a19)")]},
            now=FIXED_TIME,
        )

        self.assertEqual(
            response["choices"][0]["message"]["content"],
            "[from OpenAI-compatible Mock Model Server]: "
            "Hi, 研发 (bot_11b77a19), now time is 2026-07-24T12:34:56Z",
        )

    def test_missing_or_malformed_sender_is_rejected(self) -> None:
        payloads = [
            {"messages": []},
            {"messages": [{"role": "assistant", "content": "hello"}]},
            {"messages": [{"role": "user", "content": "@研发 hi"}]},
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Sender (untrusted metadata):\n"
                            '```json\n{"id":"human_001"}\n```'
                        ),
                    }
                ]
            },
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Sender (untrusted metadata):\n"
                            '```json\n{"label":\n```'
                        ),
                    }
                ]
            },
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Sender (untrusted metadata):\n"
                            '```json\n{"label":"   "}\n```'
                        ),
                    }
                ]
            },
        ]

        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(
                    mock_model_server.MockModelError, "missing_sender"
                ):
                    mock_model_server.sender_name(payload)

    def test_health_endpoint_returns_exact_lifecycle_response(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), mock_model_server.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address[:2]
            with urllib.request.urlopen(f"http://{host}:{port}/health") as response:
                payload = json.load(response)
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.headers.get_content_type(), "application/json"
                )
            self.assertEqual(
                payload,
                {"status": "ok", "service": "singlebox-mock-model"},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_http_endpoint_returns_non_streaming_completion(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), mock_model_server.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address[:2]
            request = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=json.dumps(
                    {
                        "model": "singlebox-mock",
                        "stream": True,
                        "messages": [user_message("Turing")],
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request) as response:
                payload = json.load(response)
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.headers.get_content_type(), "application/json"
                )
            content = payload["choices"][0]["message"]["content"]
            self.assertTrue(
                content.startswith(
                    "[from OpenAI-compatible Mock Model Server]: "
                    "Hi, Turing, now time is "
                )
            )
            self.assertRegex(content.rsplit(" ", 1)[-1], TIMESTAMP_PATTERN)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_http_endpoint_returns_timestamped_missing_sender(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), mock_model_server.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address[:2]
            request = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=json.dumps(
                    {"messages": [{"role": "user", "content": "@研发 hi"}]}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request)
            self.assertEqual(raised.exception.code, 400)
            body = json.load(raised.exception)
            self.assertEqual(body["error"], "missing_sender")
            self.assertRegex(body["timestamp"], TIMESTAMP_PATTERN)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
