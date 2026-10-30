from __future__ import annotations

import unittest

from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.task_harness.relay_session_recovery import (
    RelaySessionPreconditionError,
    RelaySessionRecovery,
    RELAY_HARNESS_RESUME_PROMPT,
    relay_harness_resume_prompt,
)
from agentclaw.community.core.task.task_harness.runtime_probe import TaskRuntimeLivenessProbe


class _FakeOpenApiBot:
    def __init__(self):
        self.calls = []
        self.messages = [
            {"id": "new", "created_at": "2026-10-20T18:17:26Z", "content": "new"},
            {
                "id": "old",
                "created_at": "2026-10-20T18:02:42Z",
                "content": "production APIs can be newest first",
            },
        ]

    async def get_session_messages(
        self, session_id, limit=20, bot_id=None, lifecycle_stage=None
    ):
        return list(self.messages)

    async def send_message(self, *, bot_id, message, metadata, message_id=None):
        self.calls.append({"bot_id": bot_id, "message": message, "metadata": metadata})
        from agentclaw.community.core.task.task_runner.client.ports import BotSendResult

        return BotSendResult(run_id="resume-run", session_id=metadata["session_id"])


class _FakeBcs:
    def __init__(self):
        self.session_calls = []

    async def get_group(self, group_id):
        assert group_id == "g1"
        return {"driver_bot": "bot-bcs-uuid"}

    async def send_session_chat(
        self, session_id, *, message, caller_bearer=None, idempotency_key=None
    ):
        self.session_calls.append(
            {
                "session_id": session_id,
                "message": message,
                "caller_bearer": caller_bearer,
            }
        )
        return {"delivered": True, "session_id": session_id, "run_id": "resume-run"}


class _FakeTokens:
    def get_token(self, bot_uuid):
        assert bot_uuid == "bot-bcs-uuid"
        return "bearer-token"


class _Node:
    def __init__(
        self, *, assignee=None, owner_id=None, group_id=None, session_id=None,
        lifecycle_stage=None,
    ):
        from agentclaw.community.core.task.domain.models import RuntimeInfo

        props = {}
        if group_id:
            props["group_id"] = group_id
        if session_id:
            props["session_id"] = session_id
        if owner_id:
            props["assignee_owner_id"] = owner_id
        if lifecycle_stage:
            props["lifecycle_stage"] = lifecycle_stage
        self.run_info = RuntimeInfo(assignee=assignee, extend_props=props)
        self.task_id = "t1"
        self.node_id = "n1"


class RelaySessionRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_openapi_resume_reuses_existing_session_and_composed_identity(self):
        bot = _FakeOpenApiBot()
        recovery = RelaySessionRecovery(bot=bot)
        node = _Node(assignee="bot1", owner_id="146836", session_id="engine-session")

        result = await recovery.resume(node)

        self.assertEqual(result.channel, "openapi_bot_session")
        self.assertEqual(result.session_id, "engine-session")
        self.assertEqual(relay_harness_resume_prompt(), RELAY_HARNESS_RESUME_PROMPT)

        self.assertEqual(
            bot.calls,
            [
                {
                    "bot_id": compose_bot_identity("bot1", "146836"),
                    "message": relay_harness_resume_prompt(),
                    "metadata": {"session_id": "engine-session"},
                }
            ],
        )

    async def test_openapi_resume_preserves_draft_lifecycle_stage(self):
        bot = _FakeOpenApiBot()
        recovery = RelaySessionRecovery(bot=bot)
        node = _Node(
            assignee="bot1",
            owner_id="146836",
            session_id="engine-session",
            lifecycle_stage="draft",
        )

        await recovery.resume(node)

        self.assertEqual(
            bot.calls[0]["metadata"],
            {
                "session_id": "engine-session",
                "bot_options": {"lifecycle_stage": "draft"},
            },
        )

    async def test_bcs_resume_posts_to_existing_group_session(self):
        bcs = _FakeBcs()
        recovery = RelaySessionRecovery(bot=object(), bcs=bcs, bot_tokens=_FakeTokens())
        node = _Node(assignee="g1", group_id="g1", session_id="group-session")

        result = await recovery.resume(node)

        self.assertEqual(result.channel, "bcs_session")
        self.assertEqual(result.dispatch_handle, "resume-run")
        self.assertEqual(
            bcs.session_calls,
            [
                {
                    "session_id": "group-session",
                    "message": relay_harness_resume_prompt(),
                    "caller_bearer": "bearer-token",
                }
            ],
        )

    async def test_all_resume_steps_use_the_same_continuation_prompt(self):
        bot = _FakeOpenApiBot()
        recovery = RelaySessionRecovery(bot=bot)
        node = _Node(assignee="bot1", owner_id="146836", session_id="engine-session")

        for resume_step in (None, "S1-S4a", "S5", "S6"):
            with self.subTest(resume_step=resume_step):
                await recovery.resume(node, resume_step=resume_step)

        self.assertEqual(
            [call["message"] for call in bot.calls],
            [RELAY_HARNESS_RESUME_PROMPT] * 4,
        )
        self.assertIn("从当前会话历史中获取 S1 到断点前已形成的上下文和结果", RELAY_HARNESS_RESUME_PROMPT)
        self.assertIn("不要重做已完成步骤", RELAY_HARNESS_RESUME_PROMPT)
        self.assertIn("从第一个未完成步骤继续执行，直到流程执行完为止", RELAY_HARNESS_RESUME_PROMPT)

    async def test_missing_session_is_a_precondition_error(self):
        recovery = RelaySessionRecovery(bot=_FakeOpenApiBot())
        with self.assertRaisesRegex(RelaySessionPreconditionError, "no existing session_id"):
            await recovery.resume(_Node(assignee="bot1"))

    async def test_placeholder_assignee_is_a_precondition_error(self):
        recovery = RelaySessionRecovery(bot=_FakeOpenApiBot())
        with self.assertRaisesRegex(RelaySessionPreconditionError, "no valid assignee"):
            await recovery.resume(
                _Node(assignee="NOT_DECIDED", session_id="engine-session")
            )

    async def test_group_without_session_is_a_precondition_error(self):
        recovery = RelaySessionRecovery(
            bot=object(), bcs=_FakeBcs(), bot_tokens=_FakeTokens()
        )
        with self.assertRaisesRegex(
            RelaySessionPreconditionError, "group node has no existing session_id"
        ):
            await recovery.resume(_Node(assignee="g1", group_id="g1"))


class RuntimeProbeMessageCursorTest(unittest.IsolatedAsyncioTestCase):
    async def test_latest_message_is_selected_from_newest_first_payload(self):
        bot = _FakeOpenApiBot()
        probe = TaskRuntimeLivenessProbe(bot=bot)
        node = _Node(session_id="engine-session")

        first = await probe.probe(node)
        # A different source position with an earlier timestamp must not count as
        # progress just because it is the list's last element.
        second = await probe.probe(node, previous_cursor=first.heartbeat_cursor)

        self.assertEqual(first.state, "active")
        self.assertFalse(first.progressed)
        self.assertEqual(second.state, "active")
        self.assertFalse(second.progressed)

    async def test_new_timestamp_progresses_even_if_messages_are_newest_first(self):
        bot = _FakeOpenApiBot()
        probe = TaskRuntimeLivenessProbe(bot=bot)
        node = _Node(session_id="engine-session")
        first = await probe.probe(node)

        bot.messages = [
            {
                "id": "newest",
                "created_at": "2026-10-20T18:20:00Z",
                "content": "continued",
            },
            {
                "id": "old",
                "created_at": "2026-10-20T18:02:42Z",
                "content": "old",
            },
        ]
        second = await probe.probe(node, previous_cursor=first.heartbeat_cursor)
        self.assertTrue(second.progressed)


if __name__ == "__main__":
    unittest.main()
