#!/usr/bin/env python3
"""Unit tests for mock_provider_downlink.py."""

import unittest

import mock_provider_downlink as mock_provider


def request(method, request_id="req-1", timestamp=1000):
    return {
        "type": "req",
        "id": request_id,
        "method": method,
        "session_id": "grp-001:feedbeef",
        "bcs_group_id": "grp-001",
        "to_bot": {
            "provider_id": "provider-local",
            "provider_bot_ref": "reviewer-v2",
        },
        "from": {
            "kind": "bot",
            "id": "driver-bot",
            "name": "Driver",
        },
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "please review"}],
            "timestamp": timestamp,
        },
        "timeout_ms": 60000,
    }


class MockProviderStateTest(unittest.TestCase):
    def test_chat_send_records_session_and_returns_ack(self):
        state = mock_provider.MockProviderState(
            provider_id="provider-local",
            final_text="mock final",
            auto_callback=False,
        )

        status, body = state.handle_webhook({}, request("chat.send", "run-1"))

        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})
        messages = state.session_messages("reviewer-v2", "grp-001:feedbeef")
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(messages[1]["content"], "mock final")

    def test_history_filters_before_and_limit(self):
        state = mock_provider.MockProviderState(
            provider_id="provider-local",
            final_text="mock final",
            auto_callback=False,
        )
        state.handle_webhook({}, request("chat.inject", "inject-1", timestamp=1000))
        state.handle_webhook({}, request("chat.inject", "inject-2", timestamp=2000))
        state.handle_webhook({}, request("chat.inject", "inject-3", timestamp=3000))

        history = request("chat.history", "history-1")
        history["before"] = 3000
        history["limit"] = 1
        status, body = state.handle_webhook({}, history)

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["session_id"], "grp-001:feedbeef")
        self.assertNotIn("session_key", body)
        self.assertEqual(body["messages"][0]["timestamp"], 2000)
        self.assertTrue(body["has_more"])
        self.assertEqual(body["next_before"], 2000)

    def test_provider_id_mismatch_is_rejected(self):
        state = mock_provider.MockProviderState(
            provider_id="provider-local",
            final_text="mock final",
            auto_callback=False,
        )
        bad = request("chat.inject", "inject-1")
        bad["to_bot"]["provider_id"] = "other-provider"

        status, body = state.handle_webhook({}, bad)

        self.assertEqual(status, 403)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "provider_id_mismatch")


if __name__ == "__main__":
    unittest.main()
