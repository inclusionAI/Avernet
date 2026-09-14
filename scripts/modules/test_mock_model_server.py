#!/usr/bin/env python3

import importlib.util
import argparse
import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


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
    def test_delay_argument(self) -> None:
        with patch("sys.argv", ["mock"]):
            self.assertEqual(mock_model_server.parse_args().response_delay_seconds, 0)
        with patch("sys.argv", ["mock", "--response-delay-seconds", "20"]):
            self.assertEqual(mock_model_server.parse_args().response_delay_seconds, 20)
        for value in ("-1", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    mock_model_server.nonnegative_seconds(value)

    def test_delay_does_not_block_health_or_invalid_requests(self) -> None:
        class DelayedHandler(mock_model_server.Handler):
            response_delay_seconds = 20

        entered = threading.Event()
        release = threading.Event()
        results = []

        def delayed_sleep(seconds):
            self.assertEqual(seconds, 20)
            entered.set()
            self.assertTrue(release.wait(5))

        server = ThreadingHTTPServer(("127.0.0.1", 0), DelayedHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}"

        def completion():
            request = urllib.request.Request(
                base_url + "/v1/chat/completions",
                data=json.dumps({"messages": [user_message("test")]}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                results.append(response.status)

        worker = threading.Thread(target=completion, daemon=True)
        try:
            with patch.object(mock_model_server.time, "sleep", side_effect=delayed_sleep) as sleep:
                worker.start()
                self.assertTrue(entered.wait(3))
                with urllib.request.urlopen(base_url + "/health", timeout=2) as response:
                    self.assertEqual(response.status, 200)
                request = urllib.request.Request(base_url + "/v1/chat/completions", data=b"{}")
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=2)
                self.assertEqual(raised.exception.code, 400)
                raised.exception.close()
                self.assertEqual(results, [])
                release.set()
                worker.join(3)
                self.assertEqual(results, [200])
                sleep.assert_called_once_with(20)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join()
            worker.join(5)

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

    def test_missing_or_malformed_sender_uses_display_fallback(self) -> None:
        payloads = [
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
                self.assertEqual(mock_model_server.sender_name(payload), "user")

    def test_fallback_does_not_reuse_historical_sender(self) -> None:
        for content in ("READY-02", [{"type": "text", "text": "READY-02"}]):
            with self.subTest(content=content):
                self.assertEqual(mock_model_server.sender_name({"messages": [
                    user_message("Old Sender"),
                    {"role": "user", "content": content},
                ]}), "user")

    def test_fallback_handles_invalid_blocks_without_logging_content(self) -> None:
        for block in (
            '```json\n{"label":"PRIVATE-PROMPT"}',
            '```json\n["PRIVATE-PROMPT"]\n```',
            '```json\n{"label":"PRIVATE-PROMPT\\ninvalid"}\n```',
            '```json\n' + json.dumps({"label": "PRIVATE-PROMPT" * 20}) + '\n```',
        ):
            with self.subTest(block=block):
                with self.assertLogs(mock_model_server.LOGGER, level="WARNING") as logs:
                    sender = mock_model_server.sender_name({"messages": [{
                        "role": "user", "content": "Sender (untrusted metadata):\n" + block,
                    }]})
                self.assertEqual(sender, "user")
                self.assertNotIn("PRIVATE-PROMPT", "\n".join(logs.output))
                self.assertIn("mock_sender_fallback", "\n".join(logs.output))

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

    def test_http_endpoint_accepts_plain_user_message(self) -> None:
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
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 200)
                body = json.load(response)
            content = body["choices"][0]["message"]["content"]
            self.assertIn("Hi, user, now time is ", content)
            self.assertRegex(content.rsplit(" ", 1)[-1], TIMESTAMP_PATTERN)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_http_endpoint_rejects_invalid_json_and_messages(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), mock_model_server.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cases = [(b"{", "invalid_json"), (b"[]", "invalid_json")]
        for messages in (None, {}, [], [None], [{}],
                         [{"role": "invalid", "content": "x"}],
                         [{"role": "user", "content": 123}],
                         [{"role": "user", "content": ["x"]}],
                         [{"role": "user", "content": [{"type": "text", "text": 1}]}]):
            cases.append((json.dumps({"messages": messages}).encode(), "invalid_messages"))
        try:
            host, port = server.server_address[:2]
            for data, expected in cases:
                with self.subTest(data=data):
                    request = urllib.request.Request(
                        f"http://{host}:{port}/v1/chat/completions", data=data,
                        headers={"Content-Type": "application/json"},
                    )
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        urllib.request.urlopen(request)
                    with raised.exception as response:
                        self.assertEqual(response.code, 400)
                        self.assertEqual(json.load(response)["error"], expected)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
