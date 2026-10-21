from __future__ import annotations

import unittest

from agentclaw.community.core.task.task_harness.runtime_probe import (
    TaskRuntimeLivenessProbe,
)


class _FakeBot:
    def __init__(self):
        self.run = {"status": "RUNNING", "version": 1}
        self.messages = []
        self.session_bot_ids = []

    async def get_run(self, run_id):
        return dict(self.run)

    async def get_session_messages(self, session_id, limit=20, bot_id=None):
        self.session_bot_ids.append(bot_id)
        return list(self.messages)


class _FakeBcs:
    def __init__(self):
        self.group = {
            "status": "running",
            "version": 1,
            "session": {"status": "running", "version": 1},
        }

    async def get_group(self, group_id):
        return dict(self.group)

    async def get_session_messages(self, session_id, limit=20, since_msg_id=None,
                                   caller_bearer=None):
        return []

    async def get_state_machine_run(self, run_id):
        raise RuntimeError("no run")


class _Node:
    def __init__(
        self,
        *,
        run_id=None,
        session_id=None,
        group_id=None,
        assignee=None,
        assignee_owner_id=None,
    ):
        from agentclaw.community.core.task.domain.models import RuntimeInfo

        self.run_info = RuntimeInfo(
            assignee=assignee,
            extend_props={
                key: value
                for key, value in {
                    "run_id": run_id,
                    "session_id": session_id,
                    "group_id": group_id,
                    "assignee_owner_id": assignee_owner_id,
                }.items()
                if value
            },
        )


class TestTaskRuntimeLivenessProbe(unittest.IsolatedAsyncioTestCase):
    async def test_single_bot_active_progress_uses_message_cursor(self):
        bot = _FakeBot()
        probe = TaskRuntimeLivenessProbe(bot=bot)
        node = _Node(run_id="r1", session_id="s1")

        first = await probe.probe(node)
        self.assertEqual(first.state, "active")
        self.assertFalse(first.progressed)

        bot.messages = [{"id": "m1"}]
        second = await probe.probe(node, previous_cursor=first.heartbeat_cursor)
        self.assertEqual(second.state, "active")
        self.assertTrue(second.progressed)

        third = await probe.probe(node, previous_cursor=second.heartbeat_cursor)
        self.assertEqual(third.state, "active")
        self.assertFalse(third.progressed)

    async def test_single_bot_terminal_failure_is_reported(self):
        bot = _FakeBot()
        bot.run = {"status": "FAILED"}
        probe = TaskRuntimeLivenessProbe(bot=bot)

        result = await probe.probe(_Node(run_id="r1"))
        self.assertEqual(result.state, "terminal_failed")
        self.assertEqual(result.status, "failed")

    async def test_session_probe_passes_composite_bot_id(self):
        bot = _FakeBot()
        probe = TaskRuntimeLivenessProbe(bot=bot)
        node = _Node(session_id="s1", assignee="b1", assignee_owner_id="u1")

        await probe.probe(node)

        self.assertEqual(bot.session_bot_ids, ["b1:u1"])

    async def test_session_probe_preserves_historical_composite_assignee(self):
        bot = _FakeBot()
        probe = TaskRuntimeLivenessProbe(bot=bot)
        node = _Node(session_id="s1", assignee="b1:u1")

        await probe.probe(node)

        self.assertEqual(bot.session_bot_ids, ["b1:u1"])

    async def test_run_with_session_heartbeat_passes_bot_id(self):
        bot = _FakeBot()
        probe = TaskRuntimeLivenessProbe(bot=bot)
        node = _Node(
            run_id="r1",
            session_id="s1",
            assignee="b1",
            assignee_owner_id="u1",
        )

        await probe.probe(node)

        self.assertEqual(bot.session_bot_ids, ["b1:u1"])

    async def test_group_terminal_success_is_reported(self):
        bcs = _FakeBcs()
        bcs.group["session"] = {"status": "completed", "version": 3}
        probe = TaskRuntimeLivenessProbe(bcs=bcs)

        result = await probe.probe(_Node(group_id="g1", session_id="s1"))
        self.assertEqual(result.state, "terminal_success")

    async def test_missing_handle_and_port_are_unknown(self):
        probe = TaskRuntimeLivenessProbe()
        self.assertEqual((await probe.probe(_Node())).state, "unknown")
        self.assertEqual(
            (await probe.probe(_Node(run_id="r1"),)).state, "unknown"
        )


if __name__ == "__main__":
    unittest.main()
