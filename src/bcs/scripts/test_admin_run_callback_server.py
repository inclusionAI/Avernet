#!/usr/bin/env python3
"""Unit tests for admin_run_callback_server.py."""

from __future__ import annotations

import json
import threading
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
        self.server = callback_server.create_server(
            "127.0.0.1",
            0,
            callback_server.ServerConfig(),
            self.store,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

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


if __name__ == "__main__":
    unittest.main()
