#!/usr/bin/env python3
"""Tests for event_webhook_receiver.py."""

from __future__ import annotations

import contextlib
import io
import json
import threading
import unittest
import urllib.request

import event_webhook_receiver as receiver


class EventWebhookReceiverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = receiver.create_server("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.url = f"http://{host}:{port}/events"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_prints_event_log_and_returns_204(self) -> None:
        body = b'{"event_id":"evt-1","event_type":"group.created"}'
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(response.read(), b"")

        record = json.loads(output.getvalue())
        self.assertEqual(record["path"], "/events")
        self.assertEqual(record["body"].encode(), body)


if __name__ == "__main__":
    unittest.main()
