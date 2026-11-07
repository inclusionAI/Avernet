"""Failed-blocking-ref fail-fast tests.

A FAILED agent blocking an ACTIVE (unsatisfied) wait ref aborts the run
through the fenced failure path — both in-slice (post-drain) and at the
rehydrate prologue for a crashed slice — with the dead agent's id,
attempts, and error in the run's error string. Boundaries: a wait-mode
ANY satisfied in the same batch as a failure still succeeds (success
beats failure), a FAILED agent on a ref nobody waits on never aborts
the run, and at most one fail-fast finalize lands per slice.

Drives SwarmRunHandler over the same real-DB harness as
test_swarm_slice.py, with a failing-fake factory for the dead agents.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om

from tests.community.core.agentswarm.runtime.handler.test_swarm_slice import (
    TENANT, WORKER, _TestDB, FakeFactory, RepeatingLLMProvider, _build_env,
    _enqueue_and_claim, _mk_run, _spawn_decision, _wait_decision,
    _finish_decision, _dispatch_row,
)
from tests.community.core.agentswarm.test_coverage_gaps import _mk_agent


@pytest.fixture()
def db():
    # StaticPool: the slice path offloads repo calls to to_thread workers.
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    yield _TestDB(engine)
    engine.dispose()


def _wait_any_decision(*refs) -> str:
    return json.dumps({"kind": "wait", "refs": list(refs), "mode": "any"})


class _AlwaysFailsFactory(FakeFactory):
    """FakeFactory whose run_task always raises (a per-script dead agent)."""

    def __init__(self, fail_names: set[str]) -> None:
        super().__init__()
        self.fail_names = fail_names

    def create(self, spec, run, agent_id=None, attempt=1):
        if spec.name in self.fail_names:
            seen = self.creates.get(spec.name, 0)
            self.creates[spec.name] = seen + 1

            class _Dead:
                def __init__(self_inner) -> None:
                    self_inner.name = spec.name
                    self_inner.executor_id = ""
                    self_inner.executor_name = spec.name

                async def setup(self_inner) -> None:
                    pass

                async def run_task(self_inner, envelope):
                    raise RuntimeError("boom forever")

                async def teardown(self_inner) -> None:
                    pass

                async def halt(self_inner) -> None:
                    pass
            return _Dead()
        return super().create(spec, run, agent_id=agent_id, attempt=attempt)


class TestFailFastOnFailedBlockingRef:
    """(b) an exhausted FAILED agent blocking the waited ref aborts the run."""

    def test_failed_waited_agent_aborts_run_fenced_failure_path(self, db):
        script = [
            _spawn_decision("a1", "objective-a1"),
            _wait_decision("a1"),
            _finish_decision("never reached"),
        ]
        factory = _AlwaysFailsFactory({"a1"})
        env = _build_env(db, script, factory)
        run = _mk_run(goal="failfast")
        claim = _enqueue_and_claim(env, run)

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))

        assert outcome == "fail"
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.FAILED
        assert stored.termination_reason is dm.TerminationReason.ERROR
        assert stored.error is not None
        (agent,) = env.agent_repo.list_by_run(run.run_id, TENANT)
        assert agent.status is dm.AgentStatus.FAILED
        assert agent.attempts == 3
        assert agent.agent_id in stored.error
        assert "attempts=3" in stored.error
        assert "boom forever" in stored.error

    def test_unwaited_failed_agent_does_not_abort_run(self, db):
        """(g) a FAILED agent nobody waits on never kills the run."""
        script = [
            _spawn_decision("a1", "objective-a1"),
            _spawn_decision("a2", "objective-a2"),
            _wait_decision("a2"),
            _finish_decision("succeeded despite the dead branch"),
        ]
        factory = _AlwaysFailsFactory({"a1"})
        env = _build_env(db, script, factory)
        run = _mk_run(goal="unwaited dead branch")
        claim = _enqueue_and_claim(env, run)

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))

        # StubLLMProvider consumes decisions one per LLM call; the spawn
        # pair decision spawns both in one cohort, then the run follows
        # the scripted wait(a2)/finish and converges on a2's result.
        assert outcome == "complete"
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.COMPLETED
        # Rows carry spawn-local composite names ("a1_<12hex>").
        agents = {a.name.split("_", 1)[0]: a
                  for a in env.agent_repo.list_by_run(run.run_id, TENANT)}
        assert agents["a1"].status is dm.AgentStatus.FAILED
        assert agents["a2"].status is dm.AgentStatus.COMPLETED

    def test_success_beats_failure_within_one_batch_any_mode(self, db):
        """(c) one batch lands a success and a failure; ANY still wins."""
        script = [
            _spawn_decision("a1", "objective-a1"),
            _spawn_decision("a2", "objective-a2"),
            _wait_any_decision("a1", "a2"),
            _finish_decision("any beat the dead branch"),
        ]
        factory = _AlwaysFailsFactory({"a1"})
        env = _build_env(db, script, factory)
        run = _mk_run(goal="success beats failure")
        claim = _enqueue_and_claim(env, run)

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))

        assert outcome == "complete"
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.COMPLETED
        assert stored.final_answer == "any beat the dead branch"


class TestRehydrateFailFast:
    """(d) a crashed slice parked mid-wait on a now-FAILED agent aborts."""

    def test_prologue_aborts_before_running_an_orchestrator_turn(self, db):
        env = _build_env(db, [], FakeFactory(),
                         llm=RepeatingLLMProvider(_finish_decision("late")))
        run = _mk_run(goal="rehydrate failfast")
        claim = _enqueue_and_claim(env, run)

        agent = _mk_agent(run, "a1")
        env.agent_repo.create(agent)
        env.agent_repo.update_status(
            run.run_id, agent.agent_id, TENANT, dm.AgentStatus.FAILED.value,
            attempts=3, error="boom forever", claim_epoch=claim["claim_epoch"],
        )
        env.message_repo.create(dm.Message(
            id=dm.MessageId("m-wait-1"), run_id=run.run_id, tenant=TENANT,
            agent_id=dm.AgentId(run.run_id),
            seq=int(time.time() * 1000) + 1,
            role=dm.MessageRole.DECISION,
            content={"kind": "wait", "refs": ["a1"], "mode": "all"},
        ))

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))

        # The prologue fires before any orchestrator LLM call: the replay
        # provider's counter must still be zero.
        assert outcome == "fail"
        assert env.llm.calls == 0
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.FAILED
        assert stored.termination_reason is dm.TerminationReason.ERROR
        assert agent.agent_id in stored.error
        assert "boom forever" in stored.error

    def test_satisfied_wait_refs_never_abort_on_resume(self, db):
        # The same crash shape, but the waited ref delivered its result
        # before the crash: the predicate stays quiet and the resumed
        # slice proceeds to the orchestrator's next decision.
        env = _build_env(db, [], FakeFactory(),
                         llm=RepeatingLLMProvider(_finish_decision("resumed")))
        run = _mk_run(goal="rehydrate healthy")
        claim = _enqueue_and_claim(env, run)
        agent = _mk_agent(run, "a1")
        agent.name = "a1"
        env.agent_repo.create(agent)
        env.result_repo.create(dm.AgentResult(
            id=dm.ResultId("result-1"), run_id=run.run_id, tenant=TENANT,
            source_agent_id=agent.agent_id, payload={"done": True},
            artifact_key=None, routed_at=dm.EpochMillis(int(time.time() * 1000)),
        ))
        env.message_repo.create(dm.Message(
            id=dm.MessageId("m-wait-2"), run_id=run.run_id, tenant=TENANT,
            agent_id=dm.AgentId(run.run_id),
            seq=int(time.time() * 1000) + 2,
            role=dm.MessageRole.DECISION,
            content={"kind": "wait", "refs": ["a1"], "mode": "all"},
        ))

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))

        assert outcome == "complete"
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.COMPLETED
        assert stored.final_answer == "resumed"


class TestSingleAbortPath:
    """(f) at most one fail-fast finalize per slice (first-fail-wins)."""

    def test_second_abort_is_ignored_single_fenced_write(self, db):
        env = _build_env(db, [], FakeFactory())
        run = _mk_run(goal="guarded abort")
        claim = _enqueue_and_claim(env, run)

        env.handler._finalized = False
        failed_status_writes: list[str] = []
        original_update = env.run_repo.update_status

        def counting_update(run_id, tenant, status, **kwargs):
            if status is dm.RunStatus.FAILED:
                failed_status_writes.append(str(status))
            return original_update(run_id, tenant, status, **kwargs)

        env.run_repo.update_status = counting_update

        failed = {"agent_id": "agent-dead", "name": "a1",
                  "attempts": 3, "error": "boom forever"}
        first = asyncio.run(env.handler._abort_failed_blocking_ref(
            run.run_id, TENANT, failed, claim["claim_epoch"],
        ))
        second = asyncio.run(env.handler._abort_failed_blocking_ref(
            run.run_id, TENANT, failed, claim["claim_epoch"],
        ))

        assert first == second == "fail"
        assert failed_status_writes == ["FAILED"]
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.FAILED
        assert "agent-dead" in stored.error


class TestDeferredUnspawnedRef:
    """An all-unspawned unsatisfied ref set is a deferral, not a failure."""

    def test_all_unspawned_refs_yield_none_not_slice_lost(self, db):
        env = _build_env(db, [], FakeFactory())
        run = _mk_run(goal="unspawned deferral")
        _enqueue_and_claim(env, run)
        env.handler._worker_id = WORKER

        async def main():
            return await env.handler._await_refs(
                run.run_id, TENANT, ("w4_a", "w6_spawn"), dm.WaitMode.ALL,
                time.monotonic() + 5.0, None,
            )

        outcome = asyncio.run(main())

        # No agent row for either ref: the slice yields cleanly (None) so
        # the orchestrator re-emits the spawn next turn. It must NOT return
        # the slice_lost sentinel nor fail the run.
        assert outcome is None


def _seed_slice_lost_agent(env, run, claim, name="sl1", error="slice_lost"):
    agent = _mk_agent(run, name)
    env.agent_repo.create(agent)
    env.agent_repo.update_status(
        run.run_id, agent.agent_id, TENANT, dm.AgentStatus.FAILED.value,
        attempts=1, error=error, claim_epoch=claim["claim_epoch"],
    )
    return agent


async def _bump(env, run, claim) -> int | None:
    from agentclaw.community.core.agentswarm.runtime.offload import run_blocking
    return await run_blocking(
        env.dispatch_repo.bump_reclaim, run.run_id, TENANT, WORKER,
        claim["claim_epoch"],
    )


class TestSliceLostResultlessReschedule:
    """A resultless FAILED slice_lost agent on an in-slice wait ref must
    trigger an immediate reschedule, not block to the slice deadline."""

    def test_reschedules_immediately_and_counts_one_reclaim(self, db):
        env = _build_env(db, [], FakeFactory())
        run = _mk_run(goal="slice_lost reschedule")
        claim = _enqueue_and_claim(env, run)
        agent = _seed_slice_lost_agent(env, run, claim)
        env.handler._worker_id = WORKER

        async def main():
            started = time.monotonic()
            sentinel = await env.handler._await_refs(
                run.run_id, TENANT, ("sl1",), dm.WaitMode.ALL,
                time.monotonic() + 5.0, None,
            )
            elapsed = time.monotonic() - started
            assert isinstance(sentinel, dict)
            assert sentinel["_reschedule_slice_lost"]["agent_id"] == agent.agent_id
            bumped = await _bump(env, run, claim)
            await env.handler._release_claim(
                run.run_id, TENANT, claim["claim_epoch"], "slice_lost resultless ref",
            )
            return elapsed, bumped

        elapsed, bumped = asyncio.run(main())

        assert elapsed < 5.0
        assert bumped == 1
        dispatch = _dispatch_row(db, run.run_id)
        assert dispatch["status"] == "READY"
        assert dispatch["reclaims"] == 1
        assert env.run_repo.get(run.run_id, TENANT).status is dm.RunStatus.RUNNING

    def test_repeated_slice_lost_trips_stuck_after_reclaim_max(self, db):
        env = _build_env(db, [], FakeFactory())
        run = _mk_run(goal="slice_lost stuck")
        claim = _enqueue_and_claim(env, run)
        agent = _seed_slice_lost_agent(env, run, claim)
        env.handler._worker_id = WORKER

        reclaim_max = env.cfg.orchestration.reclaim_max
        ref = {"agent_id": agent.agent_id, "name": "sl1",
               "attempts": 1, "error": "slice_lost"}

        async def main():
            reclaims = 0
            for _ in range(reclaim_max):
                reclaims = await _bump(env, run, claim)
                assert reclaims is not None
                if reclaims > reclaim_max:
                    return await env.handler._finalize_stuck_dispatch(
                        run.run_id, TENANT, claim["claim_epoch"], ref,
                    )
            # One more no-progress reschedule pushes past reclaim_max.
            return await env.handler._finalize_stuck_dispatch(
                run.run_id, TENANT, claim["claim_epoch"], ref,
            )

        outcome = asyncio.run(main())

        assert outcome == "stuck"
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.STUCK
        assert stored.termination_reason is dm.TerminationReason.STUCK
        assert "slice_lost reclaim circuit breaker" in (stored.error or "")
        assert str(agent.agent_id) in (stored.error or "")
        assert _dispatch_row(db, run.run_id)["status"] == "DONE"

    def test_non_slice_lost_resultless_failed_still_fails(self, db):
        """Regression pin: a genuine FAILED resultless agent still fails."""
        script = [
            _spawn_decision("a1", "objective-a1"),
            _wait_decision("a1"),
            _finish_decision("never reached"),
        ]
        factory = _AlwaysFailsFactory({"a1"})
        env = _build_env(db, script, factory)
        run = _mk_run(goal="genuine fail pin")
        claim = _enqueue_and_claim(env, run)

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))

        assert outcome == "fail"
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.FAILED
        assert "boom forever" in (stored.error or "")

class TestAwaitRefsCancelAbort:
    """A cancel requested while _await_refs is blocked must abort the wait
    promptly (the 0.25s chunk probe), not hang to the wait deadline."""

    def test_cancel_requested_during_wait_returns_promptly(self, db):
        env = _build_env(db, [], FakeFactory())
        run = _mk_run(goal="await cancel abort")
        claim = _enqueue_and_claim(env, run)
        killed = _mk_agent(run, "a1")
        env.agent_repo.create(killed)
        assert env.agent_repo.update_status(
            run.run_id, killed.agent_id, TENANT, dm.AgentStatus.KILLED.value,
            claim_epoch=claim["claim_epoch"],
        )
        assert env.dispatch_repo.request_cancel(run.run_id, TENANT)

        async def main():
            started = time.monotonic()
            result = await env.handler._await_refs(
                run.run_id, TENANT, ("a1",), dm.WaitMode.ALL,
                time.monotonic() + 10.0, None,
            )
            return time.monotonic() - started, result

        elapsed, result = asyncio.run(main())

        assert result is None
        assert elapsed < 5.0
