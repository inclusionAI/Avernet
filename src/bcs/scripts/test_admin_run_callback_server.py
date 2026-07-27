#!/usr/bin/env python3
"""Unit tests for admin_run_callback_server.py."""

from __future__ import annotations

import unittest

import admin_run_callback_server as callback_server


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


if __name__ == "__main__":
    unittest.main()
