"""Runtime status preference and existing message-observation fallback."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentclaw.community.core.task.domain.models import RuntimeInfo
from agentclaw.community.core.task.task_harness.runtime_probe import TaskRuntimeLivenessProbe
from agentclaw.community.core.task.task_runner.client.ports import BotSessionRuntimeStatus


def _runtime(**changes):
    return BotSessionRuntimeStatus.model_validate({
        "session_id": "s1", "found": True, "run_id": "engine-run", "state": "running",
        "started_at": 1000, "last_event_seq": 7, **changes,
    })


def _node():
    return SimpleNamespace(run_info=RuntimeInfo(
        assignee="b1", extend_props={
            "session_id": "s1", "assignee_owner_id": "u1", "lifecycle_stage": "draft",
        },
    ))


class TestBotRuntimeFallback(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = SimpleNamespace(
            get_session_runtime_status=AsyncMock(return_value=_runtime()),
            get_session_messages=AsyncMock(return_value=[{"id": "m1", "content": "working"}]),
        )
        self.probe = TaskRuntimeLivenessProbe(bot=self.bot)

    async def test_runtime_events_progress_without_message_read(self):
        first = await self.probe.probe(_node())
        self.bot.get_session_runtime_status.return_value = _runtime(last_event_seq=8)
        second = await self.probe.probe(_node(), previous_cursor=first.heartbeat_cursor)
        self.assertEqual(second.source, "openapi_bot.runtime")
        self.assertTrue(second.progressed)
        self.bot.get_session_messages.assert_not_awaited()

    async def test_runtime_terminal_never_falls_back(self):
        for state, expected in (("final", "terminal_success"), ("error", "terminal_failed"),
                                ("aborted", "terminal_failed")):
            with self.subTest(state=state):
                self.bot.get_session_runtime_status.return_value = _runtime(state=state)
                result = await self.probe.probe(_node())
                self.assertEqual(result.state, expected)
        self.bot.get_session_messages.assert_not_awaited()

    async def test_unavailable_runtime_uses_same_bot_stage_and_session(self):
        self.bot.get_session_runtime_status.side_effect = TimeoutError("status unavailable")
        with self.assertLogs("task.harness.runtime_probe", level="INFO") as logs:
            result = await self.probe.probe(_node())
        self.bot.get_session_messages.assert_awaited_once_with(
            "s1", limit=20, bot_id="b1:u1", lifecycle_stage="draft",
        )
        self.assertEqual(result.state, "active")
        self.assertTrue(result.baseline_reset)
        self.assertFalse(result.progressed)
        self.assertIn("status_error=TimeoutError", " ".join(logs.output))
        self.assertIn("selected_source=openapi_bot.session_fallback", " ".join(logs.output))

    async def test_fallback_observes_changes_but_not_identical_messages(self):
        self.bot.get_session_runtime_status.return_value = BotSessionRuntimeStatus(
            session_id="s1", found=False, reason="UNSUPPORTED_ENGINE",
        )
        first = await self.probe.probe(_node())
        second = await self.probe.probe(_node(), previous_cursor=first.heartbeat_cursor)
        self.assertFalse(second.baseline_reset)
        self.assertFalse(second.progressed)
        self.bot.get_session_messages.return_value = [{"id": "m2", "content": "working"}]
        third = await self.probe.probe(_node(), previous_cursor=second.heartbeat_cursor)
        self.assertTrue(third.progressed)

    async def test_switching_sources_rebaselines_without_false_progress(self):
        first = await self.probe.probe(_node())
        self.bot.get_session_runtime_status.side_effect = TimeoutError("status unavailable")
        second = await self.probe.probe(_node(), previous_cursor=first.heartbeat_cursor)
        self.assertTrue(second.baseline_reset)
        self.assertFalse(second.progressed)
        self.bot.get_session_runtime_status.side_effect = None
        third = await self.probe.probe(_node(), previous_cursor=second.heartbeat_cursor)
        self.assertTrue(third.baseline_reset)
        self.assertFalse(third.progressed)

    async def test_both_reads_fail_returns_probe_unavailable(self):
        for reason in (None, "NO_ACTIVE_RUN", "AMBIGUOUS_RUN", "UNSUPPORTED_ENGINE"):
            with self.subTest(reason=reason):
                if reason is None:
                    self.bot.get_session_runtime_status.side_effect = TimeoutError("status unavailable")
                else:
                    self.bot.get_session_runtime_status.side_effect = None
                    self.bot.get_session_runtime_status.return_value = BotSessionRuntimeStatus(
                        session_id="s1", found=False, reason=reason,
                    )
                self.bot.get_session_messages.side_effect = TimeoutError("messages unavailable")
                result = await self.probe.probe(_node(), previous_cursor="runtime:previous")
                self.assertEqual(result.state, "probe_unavailable")
                self.assertFalse(result.progressed)
                self.assertIsNone(result.heartbeat_cursor)
                self.assertEqual(result.detail["fallback_reason"], reason or "activity_query_failed")
                self.assertIn("TimeoutError", result.error)

    async def test_malformed_message_response_is_not_silence_evidence(self):
        self.bot.get_session_runtime_status.side_effect = TimeoutError("status unavailable")
        self.bot.get_session_messages.return_value = {"unexpected": "response"}
        result = await self.probe.probe(_node())
        self.assertEqual(result.state, "probe_unavailable")

    async def test_empty_messages_remain_valid_observation(self):
        self.bot.get_session_runtime_status.side_effect = TimeoutError("status unavailable")
        self.bot.get_session_messages.return_value = []
        first = await self.probe.probe(_node())
        second = await self.probe.probe(_node(), previous_cursor=first.heartbeat_cursor)
        self.assertEqual(second.state, "active")
        self.assertFalse(second.progressed)
        self.assertFalse(second.baseline_reset)
