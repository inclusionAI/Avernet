#!/usr/bin/env python3
"""Unit tests for admin_run_callback_server.py."""

from __future__ import annotations

import contextlib
import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from typing import Any

import admin_run_callback_server as callback_server


def http_json(
    url: str,
    method: str = "GET",
    body: dict[str, Any] | bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    data: bytes | None
    if isinstance(body, dict):
        data = json.dumps(body).encode("utf-8")
    else:
        data = body
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class CallbackStoreTest(unittest.TestCase):
    def test_records_completed_and_failed_callbacks(self) -> None:
        store = callback_server.CallbackStore()

        completed = store.record(
            {
                "Authorization": "Bearer callback-secret",
                "X-BCN-Provider-Id": "provider-1",
            },
            {
                "run_id": "run-completed",
                "provider_id": "provider-1",
                "status": "completed",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                },
            },
        )
        failed = store.record(
            {"X-BCN-Provider-Id": "provider-1"},
            {
                "run_id": "run-failed",
                "provider_id": "provider-1",
                "status": "failed",
                "error": {
                    "code": "ADMIN_INVOCATION_TARGET_FAILED",
                    "message": "target failed",
                },
            },
        )

        self.assertEqual(completed["run_id"], "run-completed")
        self.assertEqual(completed["headers"]["Authorization"], "<redacted>")
        self.assertEqual(completed["method"], "POST")
        self.assertEqual(completed["path"], "/callback")
        self.assertEqual(failed["body"]["status"], "failed")
        self.assertEqual(len(store.snapshot()["callbacks"]), 2)

    def test_marks_repeated_run_id_as_duplicate(self) -> None:
        store = callback_server.CallbackStore()
        body = {"run_id": "run-1", "status": "completed"}

        first = store.record({}, body)
        second = store.record({}, body)

        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(store.snapshot()["duplicate_counts"], {"run-1": 1})
        self.assertEqual(len(store.for_run("run-1")), 2)

    def test_reset_clears_callbacks_and_duplicate_counts(self) -> None:
        store = callback_server.CallbackStore()
        store.record({}, {"run_id": "run-1", "status": "completed"})
        store.record({}, {"run_id": "run-1", "status": "completed"})

        store.reset()

        self.assertEqual(
            store.snapshot(),
            {"callbacks": [], "duplicate_counts": {}},
        )
        self.assertEqual(store.for_run("run-1"), [])


class CallbackHttpServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = callback_server.CallbackStore()
        self.start_server(callback_server.ServerConfig())

    def start_server(self, config: callback_server.ServerConfig) -> None:
        self.server = callback_server.create_server(
            "127.0.0.1",
            0,
            config,
            self.store,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def restart_server(self, config: callback_server.ServerConfig) -> None:
        self.stop_server()
        self.start_server(config)

    def stop_server(self) -> None:
        if not hasattr(self, "server"):
            return
        server = self.server
        thread = self.thread
        del self.server
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    def tearDown(self) -> None:
        self.stop_server()

    def test_health_reports_callback_count(self) -> None:
        status, body = http_json(f"{self.base_url}/health")

        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "callback_count": 0})

    def test_callback_can_be_listed_and_selected_by_run_id(self) -> None:
        callback = {
            "run_id": "run-1",
            "provider_id": "provider-1",
            "status": "completed",
        }

        status, ack = http_json(
            f"{self.base_url}/callback",
            method="POST",
            body=callback,
        )
        list_status, listed = http_json(f"{self.base_url}/callbacks")
        run_status, selected = http_json(f"{self.base_url}/callbacks/run-1")

        self.assertEqual(status, 200)
        self.assertEqual(ack, {"ok": True, "recorded": True})
        self.assertEqual(list_status, 200)
        self.assertEqual(listed["callbacks"][0]["body"], callback)
        self.assertEqual(run_status, 200)
        self.assertEqual(len(selected["callbacks"]), 1)
        self.assertEqual(selected["callbacks"][0]["run_id"], "run-1")

    def test_reset_clears_recorded_callbacks(self) -> None:
        http_json(
            f"{self.base_url}/callback",
            method="POST",
            body={"run_id": "run-1", "status": "failed"},
        )

        status, body = http_json(f"{self.base_url}/reset", method="POST", body={})
        _, health = http_json(f"{self.base_url}/health")

        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "callback_count": 0})
        self.assertEqual(health["callback_count"], 0)

    def test_malformed_json_is_rejected_without_recording(self) -> None:
        status, body = http_json(
            f"{self.base_url}/callback",
            method="POST",
            body=b"{not-json",
        )
        _, health = http_json(f"{self.base_url}/health")

        self.assertEqual(status, 400)
        self.assertEqual(body, {"ok": False, "error": "invalid_json"})
        self.assertEqual(health["callback_count"], 0)

    def test_unknown_path_returns_not_found(self) -> None:
        status, body = http_json(f"{self.base_url}/missing")

        self.assertEqual(status, 404)
        self.assertEqual(body, {"ok": False, "error": "not_found"})

    def test_expected_bearer_token_is_required_before_recording(self) -> None:
        self.restart_server(
            callback_server.ServerConfig(expected_token="callback-secret")
        )
        callback = {"run_id": "run-token", "status": "completed"}

        missing_status, missing = http_json(
            f"{self.base_url}/callback",
            method="POST",
            body=callback,
        )
        wrong_status, wrong = http_json(
            f"{self.base_url}/callback",
            method="POST",
            body=callback,
            headers={"Authorization": "Bearer wrong"},
        )
        valid_status, _ = http_json(
            f"{self.base_url}/callback",
            method="POST",
            body=callback,
            headers={"Authorization": "Bearer callback-secret"},
        )

        self.assertEqual(missing_status, 401)
        self.assertEqual(missing, {"ok": False, "error": "invalid_token"})
        self.assertEqual(wrong_status, 401)
        self.assertEqual(wrong, {"ok": False, "error": "invalid_token"})
        self.assertEqual(valid_status, 200)
        self.assertEqual(len(self.store.snapshot()["callbacks"]), 1)

    def test_expected_provider_id_is_required_before_recording(self) -> None:
        self.restart_server(
            callback_server.ServerConfig(expected_provider_id="provider-1")
        )
        callback = {"run_id": "run-provider", "status": "completed"}

        missing_status, missing = http_json(
            f"{self.base_url}/callback",
            method="POST",
            body=callback,
        )
        wrong_status, wrong = http_json(
            f"{self.base_url}/callback",
            method="POST",
            body=callback,
            headers={"X-BCN-Provider-Id": "provider-2"},
        )
        valid_status, _ = http_json(
            f"{self.base_url}/callback",
            method="POST",
            body=callback,
            headers={"X-BCN-Provider-Id": "provider-1"},
        )

        self.assertEqual(missing_status, 403)
        self.assertEqual(
            missing,
            {"ok": False, "error": "provider_id_mismatch"},
        )
        self.assertEqual(wrong_status, 403)
        self.assertEqual(
            wrong,
            {"ok": False, "error": "provider_id_mismatch"},
        )
        self.assertEqual(valid_status, 200)
        self.assertEqual(len(self.store.snapshot()["callbacks"]), 1)

    def test_configured_callback_status_is_returned_after_recording(self) -> None:
        self.restart_server(callback_server.ServerConfig(response_status=500))

        status, body = http_json(
            f"{self.base_url}/callback",
            method="POST",
            body={"run_id": "run-500", "status": "failed"},
        )

        self.assertEqual(status, 500)
        self.assertEqual(body, {"ok": True, "recorded": True})
        self.assertEqual(len(self.store.snapshot()["callbacks"]), 1)

    def test_delay_applies_only_to_callback_endpoint(self) -> None:
        self.restart_server(callback_server.ServerConfig(response_delay_ms=100))

        health_started = time.perf_counter()
        health_status, _ = http_json(f"{self.base_url}/health")
        health_elapsed = time.perf_counter() - health_started
        callback_started = time.perf_counter()
        callback_status, _ = http_json(
            f"{self.base_url}/callback",
            method="POST",
            body={"run_id": "run-slow", "status": "completed"},
        )
        callback_elapsed = time.perf_counter() - callback_started

        self.assertEqual(health_status, 200)
        self.assertEqual(callback_status, 200)
        self.assertGreaterEqual(callback_elapsed, 0.08)
        self.assertGreaterEqual(callback_elapsed - health_elapsed, 0.05)


class CallbackServerCliTest(unittest.TestCase):
    def test_parser_defaults(self) -> None:
        args = callback_server.parse_args([])

        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 28081)
        self.assertIsNone(args.expected_token)
        self.assertIsNone(args.expected_provider_id)
        self.assertEqual(args.response_status, 200)
        self.assertEqual(args.response_delay_ms, 0)

    def test_parser_accepts_explicit_configuration(self) -> None:
        args = callback_server.parse_args(
            [
                "--host",
                "localhost",
                "--port",
                "31000",
                "--expected-token",
                "callback-secret",
                "--expected-provider-id",
                "provider-1",
                "--response-status",
                "503",
                "--response-delay-ms",
                "250",
            ]
        )

        self.assertEqual(args.host, "localhost")
        self.assertEqual(args.port, 31000)
        self.assertEqual(args.expected_token, "callback-secret")
        self.assertEqual(args.expected_provider_id, "provider-1")
        self.assertEqual(args.response_status, 503)
        self.assertEqual(args.response_delay_ms, 250)

    def test_parser_rejects_invalid_numeric_arguments(self) -> None:
        invalid_arguments = [
            ["--port", "-1"],
            ["--port", "65536"],
            ["--response-status", "99"],
            ["--response-status", "600"],
            ["--response-delay-ms", "-1"],
        ]

        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        callback_server.parse_args(arguments)


if __name__ == "__main__":
    unittest.main()
