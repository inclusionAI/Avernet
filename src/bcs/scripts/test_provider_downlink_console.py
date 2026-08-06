#!/usr/bin/env python3
"""Unit tests for provider_downlink_console.py."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import provider_downlink_console as provider_console


def webhook_request(method, request_id="req-1", timestamp=1000):
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


class ProviderConsoleStateTest(unittest.TestCase):
    def test_persists_agentpass_token_by_provider_bot_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "provider-state.json"
            state = provider_console.ProviderState.load(state_path)

            state.set_agentpass_token("reviewer-v2", "agentpass.header.sig")

            reloaded = provider_console.ProviderState.load(state_path)
            self.assertEqual(
                reloaded.agentpass_token("reviewer-v2"),
                "agentpass.header.sig",
            )

    def test_arg_overrides_set_agentpass_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = provider_console.ProviderState.load(Path(tmp) / "provider-state.json")
            args = provider_console.parse_args(
                ["--agentpass-token", "reviewer-v2=agentpass.header.sig"]
            )

            provider_console.apply_arg_overrides(state, args)

            self.assertEqual(
                state.agentpass_token("reviewer-v2"),
                "agentpass.header.sig",
            )

    def test_loads_tokens_and_persists_registered_bot(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "provider-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "provider_id": "provider-local",
                        "provider_admin_token": "admin-token",
                        "bcs_to_provider_token": "downlink-token",
                    }
                ),
                encoding="utf-8",
            )

            state = provider_console.ProviderState.load(state_path)
            state.upsert_bot(
                {
                    "bot_uuid": "bot-1",
                    "provider_id": "provider-local",
                    "provider_bot_ref": "reviewer-v2",
                    "bot_runtime_token": "runtime-token",
                }
            )

            reloaded = provider_console.ProviderState.load(state_path)

            self.assertEqual(reloaded.provider_id, "provider-local")
            self.assertEqual(reloaded.provider_admin_token, "admin-token")
            self.assertEqual(reloaded.bcs_to_provider_token, "downlink-token")
            self.assertEqual(reloaded.bot_runtime_token("reviewer-v2"), "runtime-token")

    def test_chat_send_records_received_and_fixed_reply_by_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = provider_console.ProviderState.load(Path(tmp) / "provider-state.json")
            state.provider_id = "provider-local"
            runtime = provider_console.ProviderRuntime(
                state=state,
                bcs_url="http://127.0.0.1:21000",
                strict_auth=False,
                auto_callback=False,
            )

            status, body = runtime.handle_webhook({}, webhook_request("chat.send", "run-1"))

            self.assertEqual(status, 200)
            self.assertEqual(body, {"ok": True})
            messages = state.session_messages("reviewer-v2", "grp-001:feedbeef")
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["role"], "user")
            self.assertEqual(messages[0]["content"], "please review")
            self.assertEqual(messages[1]["role"], "assistant")
            self.assertEqual(messages[1]["content"], "收到send类型消息：please review")

    def test_chat_inject_records_received_and_fixed_reply_without_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = provider_console.ProviderState.load(Path(tmp) / "provider-state.json")
            state.provider_id = "provider-local"
            runtime = provider_console.ProviderRuntime(
                state=state,
                bcs_url="http://127.0.0.1:21000",
                strict_auth=False,
                auto_callback=True,
            )

            status, body = runtime.handle_webhook({}, webhook_request("chat.inject", "inject-1"))

            self.assertEqual(status, 200)
            self.assertEqual(body, {"ok": True})
            messages = state.session_messages("reviewer-v2", "grp-001:feedbeef")
            self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
            self.assertEqual(messages[1]["content"], "收到inject类型消息：please review")
            self.assertEqual(state.callback_results, [])

    def test_history_reads_persisted_session_and_paginates(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = provider_console.ProviderState.load(Path(tmp) / "provider-state.json")
            state.provider_id = "provider-local"
            runtime = provider_console.ProviderRuntime(
                state=state,
                bcs_url="http://127.0.0.1:21000",
                strict_auth=False,
                auto_callback=False,
            )
            runtime.handle_webhook({}, webhook_request("chat.inject", "inject-1", timestamp=1000))
            runtime.handle_webhook({}, webhook_request("chat.inject", "inject-2", timestamp=2000))

            history = webhook_request("chat.history", "history-1")
            history["before"] = 2500
            history["limit"] = 1
            status, body = runtime.handle_webhook({}, history)

            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["session_id"], "grp-001:feedbeef")
            self.assertEqual(len(body["messages"]), 1)
            self.assertEqual(body["messages"][0]["timestamp"], 2001)
            self.assertTrue(body["has_more"])
            self.assertEqual(body["next_before"], 2001)

    def test_records_only_configured_capture_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = provider_console.ProviderState.load(Path(tmp) / "provider-state.json")
            state.provider_id = "provider-local"
            runtime = provider_console.ProviderRuntime(
                state=state,
                bcs_url="http://127.0.0.1:21000",
                strict_auth=False,
                auto_callback=False,
                capture_headers=["x-agent-lane", "X-Sandbox-Trace"],
            )
            headers = {
                "X-Agent-Lane": "gray-test",
                "x-sandbox-trace": "trace-123",
                "X-Not-Captured": "hidden",
            }

            status, _ = runtime.handle_webhook(
                headers,
                webhook_request("chat.inject", "inject-headers"),
            )

            self.assertEqual(status, 200)
            recorded = state.snapshot_requests()[0]
            self.assertEqual(
                recorded["captured_headers"],
                {
                    "x-agent-lane": "gray-test",
                    "X-Sandbox-Trace": "trace-123",
                },
            )
            self.assertNotIn("X-Not-Captured", recorded["captured_headers"])

    def test_parse_args_accepts_repeated_capture_headers(self):
        args = provider_console.parse_args([
            "--capture-header",
            "x-agent-lane",
            "--capture-header",
            "X-Sandbox-Trace",
        ])

        self.assertEqual(args.capture_header, ["x-agent-lane", "X-Sandbox-Trace"])

    def test_strict_auth_rejects_wrong_downlink_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = provider_console.ProviderState.load(Path(tmp) / "provider-state.json")
            state.provider_id = "provider-local"
            state.bcs_to_provider_token = "expected-token"
            runtime = provider_console.ProviderRuntime(
                state=state,
                bcs_url="http://127.0.0.1:21000",
                strict_auth=True,
                auto_callback=False,
            )

            status, body = runtime.handle_webhook(
                {"Authorization": "Bearer wrong-token"},
                webhook_request("chat.send", "run-1"),
            )

            self.assertEqual(status, 401)
            self.assertFalse(body["ok"])
            self.assertEqual(body["error"], "invalid_token")

    def test_callback_uses_agentpass_token_when_runtime_token_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = provider_console.ProviderState.load(Path(tmp) / "provider-state.json")
            state.provider_id = "provider-local"
            state.set_agentpass_token("reviewer-v2", "agentpass.header.sig")
            runtime = provider_console.ProviderRuntime(
                state=state,
                bcs_url="http://127.0.0.1:21000",
                strict_auth=False,
                auto_callback=False,
            )
            captured = {}

            class FakeResponse:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_value, traceback):
                    return False

                def read(self):
                    return b'{"ok":true}'

            def fake_urlopen(request, timeout):
                captured["request"] = request
                captured["timeout"] = timeout
                return FakeResponse()

            with patch.object(provider_console.urllib.request, "urlopen", fake_urlopen):
                runtime.post_bot_event("reviewer-v2", "run-agentpass", "agentpass done")

            self.assertEqual(captured["timeout"], 10)
            self.assertEqual(
                captured["request"].get_header("Authorization"),
                "Bearer agentpass.header.sig",
            )
            self.assertEqual(state.callback_results[0]["ok"], True)

    def test_callback_uses_provider_admin_token_and_bot_ref_in_provider_admin_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = provider_console.ProviderState.load(Path(tmp) / "provider-state.json")
            state.provider_id = "provider-local"
            state.provider_admin_token = "admin-token"
            state.data["provider"] = {"auth_mode": "provider_admin"}
            state.upsert_bot(
                {
                    "bot_uuid": "bot-1",
                    "provider_id": "provider-local",
                    "provider_bot_ref": "reviewer-v2",
                    "bot_runtime_token": "runtime-token",
                }
            )
            runtime = provider_console.ProviderRuntime(
                state=state,
                bcs_url="http://127.0.0.1:21000",
                strict_auth=False,
                auto_callback=False,
            )
            captured = {}

            class FakeResponse:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_value, traceback):
                    return False

                def read(self):
                    return b'{"ok":true}'

            def fake_urlopen(request, timeout):
                captured["request"] = request
                captured["timeout"] = timeout
                return FakeResponse()

            with patch.object(provider_console.urllib.request, "urlopen", fake_urlopen):
                runtime.post_bot_event("reviewer-v2", "run-admin", "admin done")

            self.assertEqual(
                captured["request"].get_header("Authorization"),
                "Bearer admin-token",
            )
            headers = {
                key.lower(): value
                for key, value in captured["request"].header_items()
            }
            self.assertEqual(headers["x-bcn-provider-bot-ref"], "reviewer-v2")
            self.assertEqual(state.callback_results[0]["auth_token_kind"], "provider_admin_token")
            self.assertEqual(state.callback_results[0]["ok"], True)

    def test_provider_register_accepts_provider_admin_auth_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = provider_console.ProviderState.load(Path(tmp) / "provider-state.json")

            class FakeClient:
                def __init__(self):
                    self.calls = []

                def register_provider(self, name, webhook_url, auth_mode):
                    self.calls.append((name, webhook_url, auth_mode))
                    return {
                        "provider_id": "provider-local",
                        "provider_admin_token": "admin-token",
                        "bcs_to_provider_token": "downlink-token",
                    }

            client = FakeClient()
            console = provider_console.Console(
                state=state,
                client=client,
                webhook_url="http://127.0.0.1:28080/webhook",
            )

            output = console.run_command("provider register --auth provider_admin")

            self.assertIn("provider registered", output)
            self.assertEqual(client.calls[0][2], "provider_admin")


if __name__ == "__main__":
    unittest.main()
