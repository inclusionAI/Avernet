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
        self.session_stages = []
        self.run_ids = []

    async def get_run(self, run_id):
        self.run_ids.append(run_id)
        return dict(self.run)

    async def get_session_messages(
        self, session_id, limit=20, bot_id=None, lifecycle_stage=None
    ):
        self.session_bot_ids.append(bot_id)
        self.session_stages.append(lifecycle_stage)
        return list(self.messages)


class _FakeBcs:
    def __init__(self):
        self.group = {
            "status": "running",
            "version": 1,
            "driver_bot_id": "bot-bcs-uuid",
            "session": {"status": "running", "version": 1},
        }
        self.message_calls = []

    async def get_group(self, group_id):
        return dict(self.group)

    async def get_session_messages(self, session_id, limit=20, since_msg_id=None,
                                   caller_bearer=None):
        self.message_calls.append(
            {
                "session_id": session_id,
                "limit": limit,
                "since_msg_id": since_msg_id,
                "caller_bearer": caller_bearer,
            }
        )
        return []

    async def get_state_machine_run(self, run_id):
        raise RuntimeError("no run")


class _FakeBotTokens:
    def get_token(self, bot_uuid):
        assert bot_uuid == "bot-bcs-uuid"
        return "bearer-token"


class _Node:
    def __init__(
        self,
        *,
        run_id=None,
        session_id=None,
        group_id=None,
        assignee=None,
        assignee_owner_id=None,
        lifecycle_stage=None,
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
                    "lifecycle_stage": lifecycle_stage,
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

    async def test_session_request_failure_is_probe_unavailable(self):
        class _TimeoutSessionBot(_FakeBot):
            async def get_session_messages(
                self, session_id, limit=20, bot_id=None, lifecycle_stage=None
            ):
                raise TimeoutError("request timeout")

        probe = TaskRuntimeLivenessProbe(bot=_TimeoutSessionBot())
        result = await probe.probe(_Node(session_id="s1"))

        self.assertEqual(result.state, "probe_unavailable")
        self.assertEqual(result.source, "openapi_bot.session")
        self.assertIn("TimeoutError", result.error or "")
        self.assertEqual(result.detail["reason"], "probe_request_failed")
        self.assertEqual(result.detail["session_id"], "s1")

    async def test_run_request_failure_is_probe_unavailable(self):
        class _TimeoutRunBot(_FakeBot):
            async def get_run(self, run_id):
                raise TimeoutError("request timeout")

        probe = TaskRuntimeLivenessProbe(bot=_TimeoutRunBot())
        result = await probe.probe(_Node(run_id="r1"))

        self.assertEqual(result.state, "probe_unavailable")
        self.assertEqual(result.source, "openapi_bot.run")
        self.assertIn("TimeoutError", result.error or "")
        self.assertEqual(result.detail["reason"], "probe_request_failed")
        self.assertEqual(result.detail["run_id"], "r1")

    async def test_single_bot_uses_latest_harness_resume_run(self):
        bot = _FakeBot()
        probe = TaskRuntimeLivenessProbe(bot=bot)
        node = _Node(run_id="stale-run", session_id="s1")
        node.run_info.extend_props["relay_harness_resume_run_id"] = "resume-run"

        result = await probe.probe(node)

        self.assertEqual(bot.run_ids, ["resume-run"])
        self.assertEqual(result.state, "active")
        self.assertEqual(result.detail["run_id"], "resume-run")
        self.assertEqual(result.detail["session_id"], "s1")

    async def test_engine_timeout_and_abort_map_to_terminal_failed(self):
        for status in ("TIMEOUT", "ABORTED"):
            with self.subTest(status=status):
                bot = _FakeBot()
                bot.run = {"status": status}
                probe = TaskRuntimeLivenessProbe(bot=bot)

                result = await probe.probe(_Node(run_id="r1"))

                self.assertEqual(result.state, "terminal_failed")
                self.assertEqual(result.status, status.lower())

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
        self.assertEqual(bot.session_stages, [None])

    async def test_session_probe_uses_persisted_lifecycle_stage(self):
        bot = _FakeBot()
        probe = TaskRuntimeLivenessProbe(bot=bot)
        node = _Node(session_id="s1", lifecycle_stage="draft")

        result = await probe.probe(node)

        self.assertEqual(bot.session_stages, ["draft"])
        self.assertEqual(result.detail, {
            "session_id": "s1",
            "lifecycle_stage": "draft",
        })

    async def test_run_heartbeat_uses_persisted_lifecycle_stage(self):
        bot = _FakeBot()
        probe = TaskRuntimeLivenessProbe(bot=bot)
        node = _Node(run_id="r1", session_id="s1", lifecycle_stage="draft")

        result = await probe.probe(node)

        self.assertEqual(bot.session_stages, ["draft"])
        self.assertEqual(result.detail["lifecycle_stage"], "draft")

    async def test_blank_lifecycle_stage_uses_default_runtime_stage(self):
        bot = _FakeBot()
        probe = TaskRuntimeLivenessProbe(bot=bot)
        node = _Node(session_id="s1", lifecycle_stage=" ")

        await probe.probe(node)

        self.assertEqual(bot.session_stages, [None])

    async def test_group_message_probe_uses_driver_bot_bearer(self):
        bcs = _FakeBcs()
        probe = TaskRuntimeLivenessProbe(bcs=bcs, bot_tokens=_FakeBotTokens())

        result = await probe.probe(
            _Node(group_id="g1", session_id="s1", assignee="g1")
        )

        self.assertEqual(result.state, "active")
        self.assertEqual(
            bcs.message_calls,
            [
                {
                    "session_id": "s1",
                    "limit": 20,
                    "since_msg_id": None,
                    "caller_bearer": "bearer-token",
                }
            ],
        )

    async def test_group_message_probe_falls_back_to_hmac_without_tokens(self):
        bcs = _FakeBcs()
        probe = TaskRuntimeLivenessProbe(bcs=bcs)

        await probe.probe(_Node(group_id="g1", session_id="s1"))

        self.assertIsNone(bcs.message_calls[0]["caller_bearer"])

    async def test_group_terminal_success_is_reported(self):
        bcs = _FakeBcs()
        bcs.group["session"] = {"status": "completed", "version": 3}
        probe = TaskRuntimeLivenessProbe(bcs=bcs)

        result = await probe.probe(_Node(group_id="g1", session_id="s1"))
        self.assertEqual(result.state, "terminal_success")

    async def test_group_request_failure_is_probe_unavailable(self):
        class _TimeoutBcs(_FakeBcs):
            async def get_group(self, group_id):
                raise TimeoutError("request timeout")

        probe = TaskRuntimeLivenessProbe(bcs=_TimeoutBcs())
        result = await probe.probe(_Node(group_id="g1", run_id="r1"))

        self.assertEqual(result.state, "probe_unavailable")
        self.assertEqual(result.source, "bcs")
        self.assertEqual(result.detail["reason"], "probe_request_failed")
        self.assertEqual(result.detail["group_id"], "g1")
        self.assertEqual(result.detail["run_id"], "r1")

    async def test_missing_handle_and_port_are_unknown(self):
        probe = TaskRuntimeLivenessProbe()
        self.assertEqual((await probe.probe(_Node())).state, "unknown")
        self.assertEqual(
            (await probe.probe(_Node(run_id="r1"),)).state, "unknown"
        )


if __name__ == "__main__":
    unittest.main()
