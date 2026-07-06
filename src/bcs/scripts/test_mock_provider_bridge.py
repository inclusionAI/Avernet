#!/usr/bin/env python3
"""Unit tests for mock_provider_bridge.py."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mock_provider_bridge


class MockProviderBridgeTest(unittest.TestCase):
    def test_build_chat_send_frame_forwards_permission_mode_when_set(self):
        frame = mock_provider_bridge.build_chat_send_frame(
            request_id="chat-1",
            session_key="session:1",
            message="hello",
            engine="claude_code",
            permission_mode="bypassPermissions",
        )

        self.assertEqual(frame["type"], "req")
        self.assertEqual(frame["method"], "chat.send")
        self.assertEqual(frame["params"]["permissionMode"], "bypassPermissions")

    def test_transform_command_output_end_to_tool_result(self):
        frame = {
            "type": "event",
            "event": "agent",
            "payload": {
                "stream": "command_output",
                "data": {
                    "toolCallId": "toolu_1",
                    "phase": "end",
                    "output": '{"__bcs_coordination__":true,"v":1}',
                    "exitCode": 0,
                    "durationMs": 123,
                    "cwd": "/tmp/work",
                },
            },
        }

        out = mock_provider_bridge.transform_event(frame, "claude_code")

        self.assertEqual(out["event"], "agent")
        data = out["data"]
        self.assertEqual(data["stream"], "tool")
        self.assertEqual(data["phase"], "result")
        self.assertEqual(data["toolCallId"], "toolu_1")
        self.assertEqual(data["result"]["content"][0]["text"], '{"__bcs_coordination__":true,"v":1}')
        self.assertFalse(data["isError"])
        self.assertEqual(data["exitCode"], 0)
        self.assertEqual(data["durationMs"], 123)
        self.assertEqual(data["cwd"], "/tmp/work")


if __name__ == "__main__":
    unittest.main()
