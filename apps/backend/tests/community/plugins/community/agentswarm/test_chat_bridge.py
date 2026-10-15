"""Cancellable chat bridge + engine session addressing tests
(async-run-and-resume 2.4/2.5).

The bridge under test is the production wiring: ``AvernetSubagent.run_task``
submits the sync ``BotPlatform.chat`` leg to the module-scoped bounded
executor (host.py) and awaits it through ``asyncio.futures.wrap_future``,
handing the leg a per-chat ``threading.Event`` on cancel — cancelling the
asyncio wrapper only cancels PENDING work; a RUNNING thread exits
cooperatively through the event (recv slices in production, the event wait
in these fakes) — that is what makes the thread-leak bound real.

Session addressing: the swarm passes no ``session_key`` at all — the live
engine only serves the conversations it knows and never auto-creates a
chat on a novel key, so per-leg freshness moved into the platform (host.py
resets the engine-served session before every chat.send). What crosses
the bridge is just the cancel registry and the default conversation.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import agentclaw.community.plugins.community.agentswarm.host as host_module
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.domain.models import (
    AgentSpec, EpochMillis, ProviderKind, RequestMetadata, Run, RunId,
    RunStatus, TaskEnvelope, TenantId,
)
from agentclaw.community.plugins.community.agentswarm.avernet_subagent import (
    AvernetSubagent,
)

_AGENT_ID = dm.AgentId("agent-1")
_BRIDGE_WORKERS = 2


def _now_ms():
    return int(time.time() * 1000)


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
        owner_id="owner-1",
    )


def _mk_spec(name="scout") -> AgentSpec:
    return AgentSpec(name=name, role="worker")


def _mk_envelope(objective="survey the logs"):
    return TaskEnvelope(
        name="scout", objective=objective, output_schema={},
        boundaries="stay read-only", parent_goal="root goal",
    )


def _ready_subagent(platform, run=None, **kwargs) -> AvernetSubagent:
    sub = AvernetSubagent(_mk_spec(), run or _mk_run(), platform,
                          _AGENT_ID, **kwargs)
    sub._bot_id = "bot-42"
    return sub


@pytest.fixture(autouse=True)
def _fresh_bridge():
    """A deterministic, per-test bridge: the module executor is lazily
    sized, so every test starts from a shut-down pool and registers its
    own bound."""
    host_module.shutdown_chat_bridge()
    host_module.set_chat_bridge_max_workers(_BRIDGE_WORKERS)
    yield
    host_module.shutdown_chat_bridge()


class _HangingChatPlatform:
    """Platform whose chat leg hard-hangs like a stuck engine WebSocket:
    it exits only through the cooperative cancel event (what the recv
    slices check for in the production transport), never on its own
    within a test's budget. Every leg records its unwind."""

    def __init__(self, hang_seconds=30.0):
        self.hang_seconds = hang_seconds
        self.started: list[float] = []
        self.finished: list[float] = []
        self.session_keys: list[str | None] = []

    def create_bot(self, *, spec, idempotency_key, owner_id=""):
        return "bot-42"

    def bot_status(self, bot_id, owner_id=""):
        return dm.BotState.READY.value

    def delete_bot(self, bot_id, owner_id=""):
        return True

    def chat(self, bot_id, message, owner_id="", session_key=None,
             cancel_event=None):
        self.started.append(time.monotonic())
        self.session_keys.append(session_key)
        if cancel_event is None:
            time.sleep(self.hang_seconds)
        else:
            cancel_event.wait(self.hang_seconds)
        self.finished.append(time.monotonic())
        return '{"ack": true}'


class TestBridgeThreadBound:

    def test_repeated_chat_timeouts_do_not_leak_bridge_threads(self):
        # K consecutive timeouts: each cancelled chat unwinds its bridge
        # worker through the cancel event, so live pool workers stay
        # within the executor's bound instead of growing one per timeout.
        platform = _HangingChatPlatform()

        async def main():
            sub = _ready_subagent(platform)
            for _ in range(6):
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        sub.run_task(_mk_envelope()), timeout=0.1)
            for _ in range(50):
                if len(platform.finished) == 6:
                    break
                await asyncio.sleep(0.02)

        asyncio.run(main())
        assert len(platform.finished) == 6, "hung legs never unwound"

        executor = host_module.chat_bridge_executor()
        worker_ids = {t.ident for t in executor._threads}
        assert len(worker_ids) <= _BRIDGE_WORKERS, (
            f"bridge grew to {len(executor._threads)} live workers")

    def test_concurrent_chats_queue_within_the_pool_bound(self):
        # Three concurrent chats on a two-worker bridge: the first two
        # occupy the workers, the third stays PENDING; cancelling its
        # coroutine cancels the pending future without ever minting a
        # third thread (the CPython wrap_future cancellation fact).
        platform = _HangingChatPlatform()

        async def main():
            sub = _ready_subagent(platform)
            tasks = [
                asyncio.create_task(sub.run_task(_mk_envelope()))
                for _ in range(3)
            ]
            await asyncio.sleep(0.15)
            assert len(platform.started) == _BRIDGE_WORKERS
            for t in tasks:
                t.cancel()
            done, pending = await asyncio.wait(
                tasks, timeout=2.0)
            assert not pending
            for _ in range(50):
                if len(platform.finished) == _BRIDGE_WORKERS:
                    break
                await asyncio.sleep(0.02)

        asyncio.run(main())
        assert len(platform.started) == _BRIDGE_WORKERS, (
            "the pool must cap in-flight engine chats, not pass them through"
        )
        executor = host_module.chat_bridge_executor()
        assert len({t.ident for t in executor._threads}) <= _BRIDGE_WORKERS

    def test_mid_conversation_cancel_releases_the_leg_within_bounds(self):
        platform = _HangingChatPlatform()

        async def main():
            sub = _ready_subagent(platform)
            task = asyncio.create_task(sub.run_task(_mk_envelope()))
            await asyncio.sleep(0.05)
            assert len(platform.started) == 1
            cancel_moment = time.monotonic()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            for _ in range(100):
                if platform.finished:
                    break
                await asyncio.sleep(0.02)
            return cancel_moment

        cancel_moment = asyncio.run(main())
        (unwind,) = platform.finished
        assert unwind - cancel_moment < 1.0

    def test_halt_unwinds_the_in_flight_chat(self):
        # The cancel registry: halt() reached from off the awaiting
        # coroutine still sets the in-flight chat's event, so teardown is
        # never hostage to the hung leg.
        platform = _HangingChatPlatform()

        async def main():
            sub = _ready_subagent(platform)
            task = asyncio.create_task(sub.run_task(_mk_envelope()))

            async def _halt_soon():
                await asyncio.sleep(0.05)
                await sub.halt()

            await asyncio.gather(task, _halt_soon())

        asyncio.run(main())
        assert platform.finished, "halt did not unwind the in-flight chat"


class TestSlowLegsLoopLiveness:

    def test_slow_create_bot_does_not_stall_the_loop(self):
        # Loop-liveness: while create_bot sleeps its 300ms in its to_thread
        # worker, a timer coroutine on the same loop keeps ticking — the
        # lease heartbeat lives there, and stalling it was the bug this
        # leg's offload (2.2) closes.
        class _SlowCreatePlatform(_HangingChatPlatform):
            def __init__(self):
                super().__init__()
                self.creates = 0

            def create_bot(self, *, spec, idempotency_key, owner_id=""):
                self.creates += 1
                time.sleep(0.3)
                return "bot-42"

        platform = _SlowCreatePlatform()
        ticks = 0

        async def main():
            nonlocal ticks
            sub = AvernetSubagent(_mk_spec(), _mk_run(), platform, _AGENT_ID)

            async def _ticker():
                nonlocal ticks
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    ticks += 1

            ticker = asyncio.create_task(_ticker())
            await sub.setup()
            await ticker

        asyncio.run(main())
        assert platform.creates == 1
        assert ticks > 3, "the loop was starved by a blocking create"

    def test_create_beyond_its_bound_fails_the_attempt_not_the_loop(self):
        # The configurable per-leg bound: a create slower than its
        # wait_for budget surfaces as asyncio.TimeoutError (an attempt
        # failure) while the loop stays responsive throughout.
        class _WedgedCreatePlatform(_HangingChatPlatform):
            def create_bot(self, *, spec, idempotency_key, owner_id=""):
                time.sleep(0.4)
                return "bot-42"

        platform = _WedgedCreatePlatform()
        ticks = 0

        async def main():
            nonlocal ticks

            async def _ticker():
                nonlocal ticks
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    ticks += 1

            ticker = asyncio.create_task(_ticker())
            sub = AvernetSubagent(
                _mk_spec(), _mk_run(), platform, _AGENT_ID,
                create_call_timeout=0.05)
            try:
                await sub.setup()
                raise AssertionError("wedged create did not time out")
            except asyncio.TimeoutError:
                pass
            finally:
                ticker.cancel()
                await asyncio.gather(ticker, return_exceptions=True)

        asyncio.run(main())
        assert ticks > 3, "the loop stalled while the bounded create waited"


class _SessionModelPlatform:
    """Recording platform modelling the session the engine serves: every
    chat appends to the session its caller addressed and answers with what
    that session has seen. The swarm now addresses nothing — the key the
    platform records for every leg is None, and fresh context is the
    platform's own reset-before-chat, not caller-derived addressing."""

    def __init__(self):
        self.sessions: dict[str | None, list[str]] = {}
        self.session_keys: list[str | None] = []

    def create_bot(self, *, spec, idempotency_key, owner_id=""):
        return "bot-42"

    def bot_status(self, bot_id, owner_id=""):
        return dm.BotState.READY.value

    def delete_bot(self, bot_id, owner_id=""):
        return True

    def chat(self, bot_id, message, owner_id="", session_key=None,
             cancel_event=None):
        self.session_keys.append(session_key)
        history = self.sessions.setdefault(session_key, [])
        history.append(message)
        return json.dumps({"history": history})


class TestSessionAddressing:

    def test_retry_attempts_pass_no_session_key(self):
        # The per-attempt key derivation is gone from the caller: a retried
        # or respawned turn never mints a novel key the live engine would
        # refuse — every leg addresses the platform's own conversation and
        # relies on its per-leg reset (host.py) for fresh context.
        platform = _SessionModelPlatform()

        async def main():
            for _ in range(3):
                sub = _ready_subagent(platform)
                await sub.run_task(_mk_envelope())

        asyncio.run(main())
        assert list(platform.sessions) == [None]
        assert platform.session_keys == [None, None, None]

    def test_the_attempt_session_machinery_is_gone(self):
        # The removal is surgical, not a parked-off flag: reaching for the
        # old per-attempt widening fails loudly instead of silently
        # minting novel engine keys again.
        with pytest.raises(TypeError):
            _ready_subagent(_SessionModelPlatform(), attempt=7)