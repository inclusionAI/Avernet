"""Tests for the AvernetSubagent plugin (bot-platform-backed subagent, D24).

Covers: create/poll/lifecycle against an in-file BotPlatform fake
(no network, no sockets — the seam is in-process), manifest/idempotency
key shape, terminal-failure handling, chat output parsing, observability
counters, teardown guarantees, and poll-deadline expiry. Factory dispatch
(no platform -> fail clear; platform present -> AvernetSubagent) is pinned
in test_coverage_gaps.

Conventions follow test_orchestrator.py: sync test defs driving async code
through asyncio.run, hand-written fakes (no MagicMock).
"""
from __future__ import annotations

import asyncio
import time

import pytest

from agentclaw.community.core.agentcompute.domain.models import (
    AgentSpecRaw, EpochMillis, ProviderId, RequestMetadata, Run, RunId,
    RunStatus, TaskEnvelope, TenantId,
)
from agentclaw.community.core.agentcompute.plugins.agents.bot import (
    AvernetSubagent,
)


def _now_ms():
    return int(time.time() * 1000)


def _mk_run(run_id="run-42") -> Run:
    return Run(
        run_id=RunId(run_id),
        tenant=TenantId("tenant-a"),
        goal="goal",
        agent_catalog=(),
        provider=ProviderId("avernet"),
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(_now_ms()),
    )


def _mk_spec(name="scout") -> AgentSpecRaw:
    return AgentSpecRaw(name=name, role="worker")


def _mk_envelope(objective="survey the logs", boundaries="stay read-only"):
    return TaskEnvelope(
        name="scout", objective=objective, output_schema={},
        boundaries=boundaries, parent_goal="root goal",
    )


class FakePlatform:
    """Hand-fake BotPlatform: scripted statuses (last one holds),
    one-shot failures, every call recorded."""

    def __init__(self, statuses=None, chat_reply='{"result": 42}',
                 fail_create=False, fail_chat=False, fail_delete=False):
        self.statuses = list(statuses or ["READY"])
        self.chat_reply = chat_reply
        self.fail_create = fail_create
        self.fail_chat = fail_chat
        self.fail_delete = fail_delete
        self.created: list[tuple[str, str]] = []
        self.status_queries: list[str] = []
        self.chats: list[tuple[str, str]] = []
        self.deletes: list[str] = []

    def create_bot(self, *, name, instructions, idempotency_key):
        if self.fail_create:
            raise RuntimeError("platform refused")
        self.created.append((name, instructions, idempotency_key))
        return "bot-42"

    def bot_status(self, bot_id):
        self.status_queries.append(bot_id)
        if len(self.statuses) > 1:
            return self.statuses.pop(0)
        return self.statuses[0]

    def chat(self, bot_id, message):
        if self.fail_chat:
            raise RuntimeError("bot unreachable")
        self.chats.append((bot_id, message))
        return self.chat_reply

    def delete_bot(self, bot_id):
        if self.fail_delete:
            raise RuntimeError("platform gone")
        self.deletes.append(bot_id)
        return True


class _RecordingObs:

    def __init__(self) -> None:
        self.outcomes: list[str] = []

    def llm_call(self, outcome, *args, **kwargs) -> None:
        self.outcomes.append(outcome)


class TestLifecycle:

    def test_setup_creates_bot_and_polls_to_ready(self):
        async def main():
            gw = FakePlatform(statuses=["PROVISIONING", "PROVISIONING", "READY"])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw)
            await sub.setup()

            assert sub._bot_id == "bot-42"
            assert len(gw.created) == 1
            name, instructions, idem = gw.created[0]
            assert name == "scout"
            assert instructions == "Complete the assigned task."
            assert idem == "run-42:scout"
            assert gw.status_queries == ["bot-42", "bot-42", "bot-42"]

        asyncio.run(main())

    def test_setup_uses_spec_instructions_when_given(self):
        async def main():
            gw = FakePlatform()
            spec = AgentSpecRaw(name="clerk", role="worker",
                                instructions="File the reports weekly.")
            sub = AvernetSubagent(spec, _mk_run(), gw)
            await sub.setup()
            _, instructions, _ = gw.created[0]
            assert instructions == "File the reports weekly."

        asyncio.run(main())

    def test_setup_create_failure_propagates_without_bot_id(self):
        async def main():
            gw = FakePlatform(fail_create=True)
            obs = _RecordingObs()
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, observability=obs)
            with pytest.raises(RuntimeError, match="platform refused"):
                await sub.setup()
            assert sub._bot_id is None
            assert obs.outcomes == ["non_retryable"]

        asyncio.run(main())

    def test_setup_terminal_failure_state_raises(self):
        async def main():
            gw = FakePlatform(statuses=["AUTHORIZATION_REJECTED"])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw)
            with pytest.raises(RuntimeError, match="creation failed.*AUTHORIZATION_REJECTED"):
                await sub.setup()

        asyncio.run(main())

    def test_poll_deadline_expires_to_timeout(self):
        async def main():
            gw = FakePlatform(statuses=["PROVISIONING"])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw)
            sub._bot_id = "bot-42"
            state = await sub._poll_ready("bot-42", timeout=0.0, interval=0.01)
            assert state == "TIMEOUT"
            assert gw.status_queries == []

        asyncio.run(main())


class TestRunTask:

    def test_run_task_parses_json_reply(self):
        async def main():
            gw = FakePlatform(chat_reply='{"result": 42}')
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw)
            await sub.setup()
            result = await sub.run_task(_mk_envelope())
            assert result.output == {"result": 42}
            bot_id, message = gw.chats[0]
            assert bot_id == "bot-42"
            assert "survey the logs" in message
            assert "stay read-only" in message

        asyncio.run(main())

    def test_run_task_wraps_non_json_reply_as_text(self):
        async def main():
            gw = FakePlatform(chat_reply="plain-text-not-json")
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw)
            await sub.setup()
            result = await sub.run_task(_mk_envelope())
            assert result.output == {"text": "plain-text-not-json"}

        asyncio.run(main())

    def test_run_task_requires_setup(self):
        async def main():
            sub = AvernetSubagent(_mk_spec(), _mk_run(), FakePlatform())
            with pytest.raises(RuntimeError, match="setup not completed"):
                await sub.run_task(_mk_envelope())

        asyncio.run(main())

    def test_run_task_chat_failure_counts_exhausted_and_raises(self):
        async def main():
            gw = FakePlatform(fail_chat=True)
            obs = _RecordingObs()
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, observability=obs)
            await sub.setup()
            with pytest.raises(RuntimeError, match="bot unreachable"):
                await sub.run_task(_mk_envelope())
            assert obs.outcomes == ["exhausted"]
            assert sub._bot_id is not None

        asyncio.run(main())


class TestTeardown:

    def test_teardown_deletes_bot_and_clears_id(self):
        async def main():
            gw = FakePlatform()
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw)
            await sub.setup()
            await sub.teardown()
            assert gw.deletes == ["bot-42"]
            assert sub._bot_id is None
            await sub.teardown()
            assert len(gw.deletes) == 1

        asyncio.run(main())

    def test_teardown_swallows_delete_failure(self):
        async def main():
            gw = FakePlatform(fail_delete=True)
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw)
            sub._bot_id = "bot-1"
            await sub.teardown()
            assert sub._bot_id is None

        asyncio.run(main())

    def test_halt_alias_delegates_to_teardown(self):
        async def main():
            gw = FakePlatform()
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw)
            await sub.setup()
            await sub.halt()
            assert gw.deletes == ["bot-42"]
            assert sub._bot_id is None

        asyncio.run(main())


class TestObservabilityWiring:

    def test_success_path_counts_ok(self):
        async def main():
            gw = FakePlatform()
            obs = _RecordingObs()
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, observability=obs)
            await sub.setup()
            await sub.run_task(_mk_envelope())
            await sub.teardown()
            assert obs.outcomes == ["ok"]

        asyncio.run(main())