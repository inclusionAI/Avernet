"""Tests for the AvernetSubagent plugin (bot-platform-backed subagent, D24).

Covers: create/poll/lifecycle against an in-file BotPlatform fake
(no network, no sockets — the seam is in-process), manifest/idempotency
key shape, terminal-failure handling, chat output parsing, teardown
guarantees, and poll-deadline expiry. Factory dispatch
(no platform -> fail clear; platform present -> AvernetSubagent) is pinned
in test_coverage_gaps.

Conventions follow test_orchestrator.py: sync test defs driving async code
through asyncio.run, hand-written fakes (no MagicMock).
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentSpec, EpochMillis, NonRetryableProvisioningError,
    ProviderKind, RequestMetadata, Run, RunId, RunStatus, TaskEnvelope,
    TenantId,
)
from agentclaw.community.plugins.community.agentswarm.bot_subagent import (
    AvernetSubagent,
)


def _now_ms():
    return int(time.time() * 1000)


_AGENT_ID = dm.AgentId("agent-1")


def _mk_run(run_id="run-42") -> Run:
    return Run(
        run_id=RunId(run_id),
        tenant=TenantId("tenant-a"),
        goal="goal",
        agent_catalog=(),
        provider=ProviderKind("avernet"),
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(_now_ms()),
    )


def _mk_spec(name="scout") -> AgentSpec:
    return AgentSpec(name=name, role="worker")


def _mk_envelope(objective="survey the logs", boundaries="stay read-only"):
    return TaskEnvelope(
        name="scout", objective=objective, output_schema={},
        boundaries=boundaries, parent_goal="root goal",
    )


class FakePlatform:
    """Hand-fake BotPlatform: scripted statuses (last one holds),
    one-shot failures, every call recorded. Signatures carry the seam's
    optional widening (owner_id/session_key/cancel_event) exactly as the
    real platform reads them."""

    def __init__(self, statuses=None, chat_reply='{"result": 42}',
                 fail_create=False, fail_chat=False, fail_delete=False,
                 bot_id="bot-42"):
        self.statuses = list(statuses or ["READY"])
        self.chat_reply = chat_reply
        self.fail_create = fail_create
        self.fail_chat = fail_chat
        self.fail_delete = fail_delete
        self.bot_id = bot_id
        self.created: list[tuple] = []
        self.status_queries: list[str] = []
        self.chats: list[tuple[str, str]] = []
        self.session_keys: list[str | None] = []
        self.cancel_events: list[threading.Event | None] = []
        self.deletes: list[str] = []

    def create_bot(self, *, spec, idempotency_key, owner_id=""):
        if self.fail_create:
            raise RuntimeError("platform refused")
        self.created.append((spec, idempotency_key))
        return self.bot_id

    def bot_status(self, bot_id, owner_id=""):
        self.status_queries.append(bot_id)
        if len(self.statuses) > 1:
            return self.statuses.pop(0)
        return self.statuses[0]

    def chat(self, bot_id, message, owner_id="", session_key=None,
             cancel_event=None):
        if self.fail_chat:
            raise RuntimeError("bot unreachable")
        self.chats.append((bot_id, message))
        self.session_keys.append(session_key)
        self.cancel_events.append(cancel_event)
        return self.chat_reply

    def delete_bot(self, bot_id, owner_id=""):
        if self.fail_delete:
            raise RuntimeError("platform gone")
        self.deletes.append(bot_id)
        return True


class TestLifecycle:

    def test_setup_creates_bot_and_polls_to_ready(self):
        async def main():
            gw = FakePlatform(statuses=["PROVISIONING", "PROVISIONING", "READY"])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            await sub.setup()

            assert sub._bot_id == "bot-42"
            assert len(gw.created) == 1
            spec, idem = gw.created[0]
            assert spec.name == "scout"
            assert spec.description == "worker"
            assert spec.instructions == ""
            assert spec.model is None
            assert idem == "run-42:agent-1:1"
            assert gw.status_queries == ["bot-42", "bot-42", "bot-42"]

        asyncio.run(main())

    def test_setup_uses_spec_instructions_when_given(self):
        async def main():
            gw = FakePlatform()
            spec = AgentSpec(name="clerk", role="worker",
                                instructions="File the reports weekly.")
            sub = AvernetSubagent(spec, _mk_run(), gw, _AGENT_ID)
            await sub.setup()
            spec, _ = gw.created[0]
            assert spec.instructions == "File the reports weekly."

        asyncio.run(main())

    def test_setup_create_failure_propagates_without_bot_id(self):
        async def main():
            gw = FakePlatform(fail_create=True)
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            with pytest.raises(RuntimeError, match="platform refused"):
                await sub.setup()
            assert sub._bot_id is None

        asyncio.run(main())

    def test_full_persona_crosses_the_seam(self):
        async def main():
            gw = FakePlatform()
            spec = AgentSpec(name="clerk", role="filing clerk",
                             instructions="File weekly.",
                             model="clerk-7b", metadata={"tier": "1"})
            sub = AvernetSubagent(spec, _mk_run(), gw, _AGENT_ID)
            await sub.setup()
            create_spec, idem = gw.created[0]
            assert create_spec.name == "clerk"
            assert create_spec.description == "filing clerk"
            assert create_spec.instructions == "File weekly."
            assert create_spec.model == "clerk-7b"
            assert create_spec.metadata == {"tier": "1"}
            assert idem == "run-42:agent-1:1"

        asyncio.run(main())

    def test_config_manifest_crosses_the_seam_verbatim(self):
        # The catalog spec's manifest document is the ONE per-agent
        # parameter for rich bot scenes; the subagent must carry it into
        # BotCreateSpec untouched (the host adapter, not the subagent,
        # decides what a blank document means).
        async def main():
            gw = FakePlatform()
            document = (
                "schema_version: 1\nmanifest:\n  identity:\n"
                "    - type: SOUL.md\n      content: 'be meticulous'\n"
                "  skills: []\nscript:\n  body: 'echo provisioned'\n"
            )
            spec = AgentSpec(name="clerk", role="filing clerk",
                             config_manifest=document)
            sub = AvernetSubagent(spec, _mk_run(), gw, _AGENT_ID)
            await sub.setup()
            create_spec, _ = gw.created[0]
            assert create_spec.config_manifest == document

        asyncio.run(main())

    def test_blank_config_manifest_crosses_as_blank(self):
        async def main():
            gw = FakePlatform()
            spec = AgentSpec(name="clerk", role="worker")
            sub = AvernetSubagent(spec, _mk_run(), gw, _AGENT_ID)
            await sub.setup()
            create_spec, _ = gw.created[0]
            assert create_spec.config_manifest == ""

        asyncio.run(main())

    def test_empty_bot_id_fails_fast_without_polling(self):
        async def main():
            gw = FakePlatform(statuses=["PROVISIONING"], bot_id="")
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            with pytest.raises(RuntimeError, match="invalid bot_id"):
                await sub.setup()
            assert gw.status_queries == []   # never polled a None id

        asyncio.run(main())

    def test_blank_bot_id_fails_fast_without_polling(self):
        async def main():
            gw = FakePlatform(bot_id="   ")
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            with pytest.raises(RuntimeError, match="invalid bot_id"):
                await sub.setup()
            assert gw.status_queries == []

        asyncio.run(main())

    def test_same_name_agents_get_distinct_identities(self):
        # The key is the spawn's agent identity, not the persona name: two
        # spawns of one catalog persona provision two bots, and one's
        # teardown cannot delete the other's.
        async def main():
            gw = FakePlatform()
            spec = _mk_spec(name="scout")
            first = AvernetSubagent(
                spec, _mk_run(), gw, agent_id=dm.AgentId("agent-one"))
            second = AvernetSubagent(
                spec, _mk_run(), gw, agent_id=dm.AgentId("agent-two"))
            await first.setup()
            await second.setup()
            idems = [idem for _, idem in gw.created]
            assert idems == ["run-42:agent-one:1", "run-42:agent-two:1"]

        asyncio.run(main())

    def test_same_agent_identity_rebinds_across_teardown(self):
        async def main():
            gw = FakePlatform()
            spec = _mk_spec()
            first = AvernetSubagent(
                spec, _mk_run(), gw, agent_id=dm.AgentId("agent-1"))
            await first.setup()
            first_idem = gw.created[0][1]
            await first.teardown()
            second = AvernetSubagent(
                spec, _mk_run(), gw, agent_id=dm.AgentId("agent-1"))
            await second.setup()
            assert gw.created[1][1] == first_idem

        asyncio.run(main())

    def test_retried_setup_rebinds_the_same_persona(self):
        async def main():
            gw = FakePlatform()
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            await sub.setup()
            await sub.teardown()
            await sub.setup()
            assert [idem for _, idem in gw.created] == [
                "run-42:agent-1:1", "run-42:agent-1:1"]

        asyncio.run(main())

    def test_setup_terminal_failure_state_raises(self):
        async def main():
            gw = FakePlatform(statuses=["AUTHORIZATION_REJECTED"])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            with pytest.raises(RuntimeError, match="creation failed.*AUTHORIZATION_REJECTED"):
                await sub.setup()

        asyncio.run(main())

    def test_poll_deadline_expires_to_non_retryable(self):
        async def main():
            gw = FakePlatform(statuses=["PROVISIONING"])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            sub._bot_id = "bot-42"
            with pytest.raises(NonRetryableProvisioningError):
                await sub._poll_ready("bot-42", timeout=0.0, interval=0.01)
            assert gw.status_queries == []

        asyncio.run(main())


class TestPerAttemptIdentity:
    """D-J: attempt enters the persona idempotency key and the exposed
    executor surfaces — a same-slice retry mints a fresh identity, a
    cross-slice replay re-derives the crashed slice's (adopt input)."""

    def _sub(self, platform, attempt=1, agent_id="agent-1", spec=None):
        return AvernetSubagent(spec or _mk_spec(), _mk_run(), platform,
                               dm.AgentId(agent_id), attempt=attempt)

    def test_attempt_two_mints_a_distinct_identity(self):
        async def main():
            platform = FakePlatform()
            first = self._sub(platform, attempt=1)
            retry = self._sub(platform, attempt=2)
            await first.setup()
            await retry.setup()
            return platform

        platform = asyncio.run(main())
        keys = [key for _, key in platform.created]
        assert keys == ["run-42:agent-1:1", "run-42:agent-1:2"]
        assert keys[0] != keys[1]

    def test_same_attempt_replay_rederives_the_adopt_identity(self):
        async def main():
            platform = FakePlatform()
            first = self._sub(platform, attempt=2)
            replay = self._sub(platform, attempt=2)
            await first.setup()
            await first.teardown()
            await replay.setup()
            return platform

        platform = asyncio.run(main())
        keys = [key for _, key in platform.created]
        assert keys == ["run-42:agent-1:2", "run-42:agent-1:2"]

    def test_executor_surfaces_exposed_only_after_setup(self):
        async def main():
            platform = FakePlatform(bot_id="ac_alive234567890ab")
            sub = self._sub(platform, attempt=1)
            pre_id, pre_name = sub.executor_id, sub.executor_name
            await sub.setup()
            return sub, pre_id, pre_name

        sub, pre_id, pre_name = asyncio.run(main())
        assert pre_id == ""
        assert pre_name == "scout"
        assert sub.executor_id == "ac_alive234567890ab"
        from agentclaw.community.core.agentswarm.domain.models import (
            spawn_digest,
        )
        assert sub.executor_name == (
            f"scout_{spawn_digest('run-42', 'agent-1', 1, 12)}")


class TestRunTask:

    def test_run_task_parses_json_reply(self):
        async def main():
            gw = FakePlatform(chat_reply='{"result": 42}')
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
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
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            await sub.setup()
            result = await sub.run_task(_mk_envelope())
            assert result.output == {"text": "plain-text-not-json"}

        asyncio.run(main())

    def test_run_task_requires_setup(self):
        async def main():
            sub = AvernetSubagent(_mk_spec(), _mk_run(), FakePlatform(), _AGENT_ID)
            with pytest.raises(RuntimeError, match="setup not completed"):
                await sub.run_task(_mk_envelope())

        asyncio.run(main())

    def test_run_task_chat_failure_counts_exhausted_and_raises(self):
        async def main():
            gw = FakePlatform(fail_chat=True)
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            await sub.setup()
            with pytest.raises(RuntimeError, match="bot unreachable"):
                await sub.run_task(_mk_envelope())
            assert sub._bot_id is not None

        asyncio.run(main())

    def test_run_task_passes_no_session_key(self):
        # The chat call carries no ``session_key``: the platform addresses
        # the engine-served conversation and resets it per leg (host.py),
        # so attempts never mint novel keys the live engine would refuse.
        # The cancel event still crosses the seam for cooperative unwind.
        async def main():
            gw = FakePlatform()
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            await sub.setup()
            await sub.run_task(_mk_envelope())
            assert gw.session_keys == [None]
            assert gw.cancel_events[0] is not None

        asyncio.run(main())

    def test_retried_attempts_keep_passing_no_session_key(self):
        # Freshness is per construction on the platform side (its
        # reset-before-chat), not a caller-derived conversation id: every
        # attempt of every construction passes exactly no session key.
        async def main():
            gw = FakePlatform()
            for _ in range(3):
                sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
                await sub.setup()
                sub._bot_id = "bot-42"
                await sub.run_task(_mk_envelope())
            assert gw.session_keys == [None, None, None]

        asyncio.run(main())


class TestTeardown:

    def test_teardown_deletes_bot_and_clears_id(self):
        async def main():
            gw = FakePlatform()
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
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
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            sub._bot_id = "bot-1"
            await sub.teardown()
            assert sub._bot_id is None

        asyncio.run(main())

    def test_halt_alias_delegates_to_teardown(self):
        async def main():
            gw = FakePlatform()
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            await sub.setup()
            await sub.halt()
            assert gw.deletes == ["bot-42"]
            assert sub._bot_id is None

        asyncio.run(main())


class TestChatOffloadsLongCalls:

    def test_event_loop_keeps_running_while_chat_waits(self):
        # The seam is sync; a chat is an engine round trip in seconds. This
        # pins the asyncio.to_thread offload: while chat sleeps in its
        # thread, a coroutine on the SAME loop keeps ticking — the lease
        # heartbeat lives there, and stalling it was the spurious-reclaim
        # bug (#4).
        import time as _time

        class _SlowChatPlatform(FakePlatform):
            def chat(self, bot_id, message, owner_id="", session_key=None,
                     cancel_event=None):
                _time.sleep(0.15)
                return '{"result": "done"}'

        async def main():
            gw = _SlowChatPlatform()
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            await sub.setup()
            ticks = 0

            async def _ticker():
                nonlocal ticks
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    ticks += 1

            ticker = asyncio.create_task(_ticker())
            result = await sub.run_task(_mk_envelope())
            await ticker
            return result, ticks

        result, ticks = asyncio.run(main())
        assert result.output == {"result": "done"}
        assert ticks > 1, "the loop was starved by a blocking chat"
