"""SliceStore claim-fence tests (D28): persist_completion is slice-owned.

A fenced-off update_status (claim lost to a reclaimer) must persist
nothing further and unwind as the handler's lost-claim signal, so the
result/message exactly-once convergence (D24) stays with the new owner.

Conventions follow the pool suite: sync test defs, hand-written fakes
(no MagicMock), real SQLite StaticPool via the handler-suite _TestDB.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.agent_attempt import (
    AgentAttemptRepository,
)
from agentclaw.community.core.agentswarm.repository.run_dispatch import RunDispatchRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.repository.agent_result import AgentResultRepository
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig,
)
from agentclaw.community.core.agentswarm.runtime.handler.impl import _SliceLost
from agentclaw.community.core.agentswarm.runtime.store import SliceStore

from tests.community.core.agentswarm.runtime.handler.test_swarm_slice import (
    TENANT, _TestDB, _mk_run,
)
from tests.community.core.agentswarm.test_coverage_gaps import _mk_agent

FENCE_WORKER = "fence-worker-1"
RECLAIMER = "fence-reclaimer-1"


class TestPersistCompletionClaimFence:

    @pytest.fixture()
    def db(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
        yield _TestDB(engine)
        engine.dispose()

    def _store(self, db) -> SliceStore:
        return SliceStore(
            AgentSwarmConfig(),
            AgentRepository(db), MessageRepository(db),
            AgentResultRepository(db), RoundRepository(db),
            lost_probe=lambda: False,
        )

    def _claim(self, db, run) -> int:
        dispatch_repo = RunDispatchRepository(db)
        dispatch_repo.enqueue(run_id=run.run_id, tenant=run.tenant,
                         deadline_seconds=3600)
        return dispatch_repo.claim_batch(
            FENCE_WORKER, [run.tenant], 1, 0,
        )[0]["claim_epoch"]

    @staticmethod
    def _completion(run, agent_id, result=None) -> dict:
        return {
            "run_id": run.run_id, "tenant": run.tenant,
            "agent_id": agent_id, "status": dm.AgentStatus.COMPLETED,
            "result": result if result is not None else {"done": True},
            "artifact_key": None, "error": None,
            "attempts": 1, "token_usage": dm.TokenUsage(1, 1, 2),
        }

    def test_fenced_off_persist_writes_nothing_and_raises_lost_claim(self, db):
        run = _mk_run(goal="fence")
        RunRepository(db).create(run)
        agent = _mk_agent(run, "a1")
        AgentRepository(db).create(agent)
        lost_epoch = self._claim(db, run)
        # the reclaimer takes the dispatch over between renewal ticks
        time.sleep(0.02)
        reclaims = RunDispatchRepository(db).reclaim_batch(RECLAIMER, 1, 60)
        assert [r["run_id"] for r in reclaims] == [run.run_id]

        store = self._store(db)
        with pytest.raises(_SliceLost):
            asyncio.run(store.persist_completion(
                self._completion(run, agent.agent_id), claim_epoch=lost_epoch,
            ))

        stored = AgentRepository(db).get(run.run_id, agent.agent_id, TENANT)
        assert stored.status is dm.AgentStatus.QUEUED
        assert AgentResultRepository(db).get_by_run_agent(
            run.run_id, agent.agent_id, TENANT,
        ) is None
        assert MessageRepository(db).list_by_agent(
            run.run_id, agent.agent_id, TENANT,
        ) == []

    def test_matching_epoch_persists_result_and_message(self, db):
        run = _mk_run(goal="fence-open")
        RunRepository(db).create(run)
        agent = _mk_agent(run, "a1")
        AgentRepository(db).create(agent)
        epoch = self._claim(db, run)

        store = self._store(db)
        asyncio.run(store.persist_completion(
            self._completion(run, agent.agent_id, result={"r": 1}),
            claim_epoch=epoch,
        ))

        result = AgentResultRepository(db).get_by_run_agent(
            run.run_id, agent.agent_id, TENANT,
        )
        assert result is not None and result.payload == {"r": 1}
        (message,) = MessageRepository(db).list_by_agent(
            run.run_id, agent.agent_id, TENANT,
        )
        assert message.content == {"status": "COMPLETED", "result": {"r": 1}}


class TestRecordAgentBindingJournal:
    """Store-level binding journal (D-L): fence + legacy LATEST columns.

    Uses the real AgentAttemptRepository against the same SQLite StaticPool
    engine, so the journal path is proven against the ledger table —
    including the IntegrityError convergence of a replayed ledger write.
    """

    @pytest.fixture()
    def db(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
        yield _TestDB(engine)
        engine.dispose()

    def _store(self, db) -> SliceStore:
        return SliceStore(
            AgentSwarmConfig(),
            AgentRepository(db), MessageRepository(db),
            AgentResultRepository(db), RoundRepository(db),
            lost_probe=lambda: False,
            attempt_repo=AgentAttemptRepository(db),
        )

    def _claim(self, db, run) -> int:
        dispatch_repo = RunDispatchRepository(db)
        dispatch_repo.enqueue(run_id=run.run_id, tenant=run.tenant,
                         deadline_seconds=3600)
        return dispatch_repo.claim_batch(FENCE_WORKER, [run.tenant], 1, 0)[0][
            "claim_epoch"]

    def test_matching_epoch_journals_row_and_updates_legacy_latest(self, db):
        run = _mk_run(goal="journal-open")
        RunRepository(db).create(run)
        agent = _mk_agent(run, "bot-agent")
        agent.kind = dm.AgentKind.BOT
        AgentRepository(db).create(agent)
        epoch = self._claim(db, run)

        asyncio.run(self._store(db).record_agent_binding(
            run, agent.agent_id, epoch, attempt=2,
            executor_kind=dm.AgentKind.BOT.value,
            executor_id="ac_personaid", executor_name="scout-ab12cd34",
        ))

        (row,) = AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT)
        assert row["executor_id"] == "ac_personaid"
        assert row["slice_retry_index"] == 2
        assert row["claim_epoch"] == epoch
        stored = AgentRepository(db).get(run.run_id, agent.agent_id, TENANT)
        # The legacy pair carries the LATEST persona: same write, bot kind.
        assert stored.bot_id == dm.BotId("ac_personaid")
        assert stored.bot_state is dm.BotState.READY

    def test_stale_epoch_journal_is_inert_and_unwinds(self, db):
        run = _mk_run(goal="journal-fenced")
        RunRepository(db).create(run)
        agent = _mk_agent(run, "bot-agent")
        agent.kind = dm.AgentKind.BOT
        AgentRepository(db).create(agent)
        lost_epoch = self._claim(db, run)
        time.sleep(0.02)
        RunDispatchRepository(db).reclaim_batch(RECLAIMER, 1, 60)

        with pytest.raises(_SliceLost):
            asyncio.run(self._store(db).record_agent_binding(
                run, agent.agent_id, lost_epoch, attempt=1,
                executor_kind=dm.AgentKind.BOT.value,
                executor_id="ac_personaid", executor_name="scout-ab12cd34",
            ))

        assert AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT) == []
        stored = AgentRepository(db).get(run.run_id, agent.agent_id, TENANT)
        assert stored.bot_id is None and stored.bot_state is None

    def test_llm_kind_journals_without_touching_legacy_columns(self, db):
        run = _mk_run(goal="journal-llm")
        RunRepository(db).create(run)
        agent = _mk_agent(run, "llm-agent")
        AgentRepository(db).create(agent)
        epoch = self._claim(db, run)

        asyncio.run(self._store(db).record_agent_binding(
            run, agent.agent_id, epoch, attempt=1,
            executor_kind=dm.AgentKind.LLM.value,
            executor_id="0123456789abcdef", executor_name="clerk-01234567",
        ))

        (row,) = AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT)
        assert row["executor_kind"] == dm.AgentKind.LLM.value
        stored = AgentRepository(db).get(run.run_id, agent.agent_id, TENANT)
        assert stored.bot_id is None and stored.bot_state is None

    def test_replayed_journal_write_converges(self, db):
        run = _mk_run(goal="journal-replay")
        RunRepository(db).create(run)
        agent = _mk_agent(run, "bot-agent")
        AgentRepository(db).create(agent)
        epoch = self._claim(db, run)

        asyncio.run(self._store(db).record_agent_binding(
            run, agent.agent_id, epoch, attempt=1,
            executor_kind=dm.AgentKind.BOT.value,
            executor_id="ac_personaid", executor_name="scout-ab12cd34",
        ))
        asyncio.run(self._store(db).record_agent_binding(
            run, agent.agent_id, epoch, attempt=1,
            executor_kind=dm.AgentKind.BOT.value,
            executor_id="ac_personaid", executor_name="scout-ab12cd34",
        ))

        assert len(AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT)) == 1

    def test_missing_agent_row_does_not_fail_the_journal(self, db):
        # The pool worker can run setup and journal before the handler's
        # create_agent lands the agent row (they race on one loop): the
        # ledger row is the authoritative destroy record, so the legacy
        # LATEST update is best-effort and never wedges the journal.
        run = _mk_run(goal="journal-race")
        RunRepository(db).create(run)
        epoch = self._claim(db, run)

        asyncio.run(self._store(db).record_agent_binding(
            run, dm.AgentId("agent-not-yet-created"), epoch, attempt=1,
            executor_kind=dm.AgentKind.BOT.value,
            executor_id="ac_personaid", executor_name="scout-ab12cd34",
        ))

        (row,) = AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT)
        assert row["executor_id"] == "ac_personaid"

    def test_missing_attempt_repo_is_a_no_op_composition(self, db):
        run = _mk_run(goal="journal-norepo")
        RunRepository(db).create(run)
        epoch = self._claim(db, run)

        # The guard_repo=None no-op convention: a store composed without
        # the ledger simply does not journal and never raises.
        asyncio.run(SliceStore(
            AgentSwarmConfig(),
            AgentRepository(db), MessageRepository(db),
            AgentResultRepository(db), RoundRepository(db),
            lost_probe=lambda: False,
        ).record_agent_binding(
            run, dm.AgentId("agent-x"), epoch, attempt=1,
            executor_kind=dm.AgentKind.BOT.value,
            executor_id="ac_personaid", executor_name="scout",
        ))
        assert AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT) == []


class TestWriteAheadIntentAndSettle:
    """Write-ahead binding intent (pre-setup) with post-setup settlement.

    The intent row exists even when setup() fails (so the terminal
    destroy sweep can reconcile a partially created persona), and the
    post-setup settle flips the SAME attempt row to BOUND with the real
    persona identity — one row per attempt either way.
    """

    @pytest.fixture()
    def db(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
        yield _TestDB(engine)
        engine.dispose()

    def _store(self, db) -> SliceStore:
        return SliceStore(
            AgentSwarmConfig(),
            AgentRepository(db), MessageRepository(db),
            AgentResultRepository(db), RoundRepository(db),
            lost_probe=lambda: False,
            attempt_repo=AgentAttemptRepository(db),
        )

    def _claim(self, db, run) -> int:
        dispatch_repo = RunDispatchRepository(db)
        dispatch_repo.enqueue(run_id=run.run_id, tenant=run.tenant,
                         deadline_seconds=3600)
        return dispatch_repo.claim_batch(FENCE_WORKER, [run.tenant], 1, 0)[0][
            "claim_epoch"]

    def test_intent_row_exists_on_setup_failure_settled_on_success(self, db):
        run = _mk_run(goal="write-ahead")
        RunRepository(db).create(run)
        agent = _mk_agent(run, "bot-agent")
        agent.kind = dm.AgentKind.BOT
        AgentRepository(db).create(agent)
        epoch = self._claim(db, run)
        store = self._store(db)

        # Intent lands before setup, executor_id still blank (bot kind).
        asyncio.run(store.journal_attempt_intent(
            run, agent.agent_id, epoch, attempt=1,
            executor_kind=dm.AgentKind.BOT.value,
            executor_id="", executor_name="scout",
        ))
        (intent,) = AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT)
        assert intent["executor_id"] == ""
        assert intent["binding_state"] == "SPAWNED"
        assert intent["slice_retry_index"] == 1
        assert intent["claim_epoch"] == epoch

        # Setup failed: no settle — the row stays attempted-but-blank, the
        # state the terminal destroy sweep reconciles by derived identity.
        rows = AgentAttemptRepository(db).list_agent_bindings(run.run_id, TENANT)
        assert len(rows) == 1 and rows[0]["binding_state"] == "SPAWNED"

        # A successful attempt: the settle updates the SAME row to BOUND.
        asyncio.run(store.record_agent_binding(
            run, agent.agent_id, epoch, attempt=1,
            executor_kind=dm.AgentKind.BOT.value,
            executor_id="ac_personaid", executor_name="scout-ab12cd34",
        ))
        (settled,) = AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT)
        assert settled["executor_id"] == "ac_personaid"
        assert settled["binding_state"] == "BOUND"
        assert settled["slice_retry_index"] == 1

    def test_stale_epoch_intent_is_inert_and_unwinds(self, db):
        run = _mk_run(goal="intent-fenced")
        RunRepository(db).create(run)
        agent = _mk_agent(run, "bot-agent")
        AgentRepository(db).create(agent)
        lost_epoch = self._claim(db, run)
        time.sleep(0.02)
        RunDispatchRepository(db).reclaim_batch(RECLAIMER, 1, 60)

        with pytest.raises(_SliceLost):
            asyncio.run(self._store(db).journal_attempt_intent(
                run, agent.agent_id, lost_epoch, attempt=1,
                executor_kind=dm.AgentKind.BOT.value,
                executor_id="", executor_name="scout",
            ))

        assert AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT) == []

    def test_predawn_intent_settle_replay_is_idempotent(self, db):
        run = _mk_run(goal="intent-replay")
        RunRepository(db).create(run)
        agent = _mk_agent(run, "bot-agent")
        AgentRepository(db).create(agent)
        epoch = self._claim(db, run)
        store = self._store(db)

        asyncio.run(store.journal_attempt_intent(
            run, agent.agent_id, epoch, attempt=2,
            executor_kind=dm.AgentKind.BOT.value,
            executor_id="", executor_name="scout",
        ))
        asyncio.run(store.record_agent_binding(
            run, agent.agent_id, epoch, attempt=2,
            executor_kind=dm.AgentKind.BOT.value,
            executor_id="ac_personaid", executor_name="scout-ab12cd34",
        ))
        # A settle replay (crash after the UPDATE, before the caller acked)
        # converges on the same single BOUND row.
        asyncio.run(store.record_agent_binding(
            run, agent.agent_id, epoch, attempt=2,
            executor_kind=dm.AgentKind.BOT.value,
            executor_id="ac_personaid", executor_name="scout-ab12cd34",
        ))
        rows = AgentAttemptRepository(db).list_agent_bindings(run.run_id, TENANT)
        assert len(rows) == 1 and rows[0]["binding_state"] == "BOUND"

    def test_missing_attempt_repo_intent_is_a_no_op_composition(self, db):
        run = _mk_run(goal="intent-norepo")
        RunRepository(db).create(run)
        epoch = self._claim(db, run)

        asyncio.run(SliceStore(
            AgentSwarmConfig(),
            AgentRepository(db), MessageRepository(db),
            AgentResultRepository(db), RoundRepository(db),
            lost_probe=lambda: False,
        ).journal_attempt_intent(
            run, dm.AgentId("agent-x"), epoch, attempt=1,
            executor_kind=dm.AgentKind.BOT.value,
            executor_id="", executor_name="scout",
        ))
        assert AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT) == []


class TestFirstFailedWaitRef:
    """The shared fail-fast predicate: only FAILED+resultless blocking refs."""

    @pytest.fixture()
    def db(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
        yield _TestDB(engine)
        engine.dispose()

    def _store(self, db) -> SliceStore:
        return SliceStore(
            AgentSwarmConfig(),
            AgentRepository(db), MessageRepository(db),
            AgentResultRepository(db), RoundRepository(db),
            lost_probe=lambda: False,
        )

    def _claim(self, db, run) -> int:
        dispatch_repo = RunDispatchRepository(db)
        dispatch_repo.enqueue(run_id=run.run_id, tenant=TENANT, deadline_seconds=3600)
        return dispatch_repo.claim_batch(FENCE_WORKER, [TENANT], 1, 0)[0][
            "claim_epoch"]

    def _fail_agent(self, db, run, agent, error: str = "boom") -> None:
        epoch = self._claim(db, run)
        assert AgentRepository(db).update_status(
            run.run_id, agent.agent_id, TENANT, dm.AgentStatus.FAILED.value,
            attempts=3, error=error, claim_epoch=epoch,
        )

    def test_failed_agent_with_result_and_running_agent_stay_quiet(self, db):
        run = _mk_run(goal="predicate-quiet")
        RunRepository(db).create(run)
        failed = _mk_agent(run, "a1")
        running = _mk_agent(run, "a2")
        AgentRepository(db).create(failed)
        AgentRepository(db).create(running)
        self._fail_agent(db, run, failed)
        store = self._store(db)
        # A FAILED agent nobody waits on, and a RUNNING agent, never block.
        assert asyncio.run(store.first_failed_wait_ref(
            run.run_id, TENANT, (),
        )) is None
        assert asyncio.run(store.first_failed_wait_ref(
            run.run_id, TENANT, ("a2",),
        )) is None
        # A FAILED agent that delivered a result cannot block the ref.
        AgentResultRepository(db).create(dm.AgentResult(
            id=dm.ResultId("sh-1"), run_id=run.run_id, tenant=TENANT,
            source_agent_id=failed.agent_id, payload={"ok": True},
            artifact_key=None, routed_at=dm.EpochMillis(int(time.time() * 1000)),
        ))
        assert asyncio.run(store.first_failed_wait_ref(
            run.run_id, TENANT, ("a1",),
        )) is None

    def test_failed_resultless_agent_is_the_blocking_ref(self, db):
        run = _mk_run(goal="predicate-fire")
        RunRepository(db).create(run)
        failed = _mk_agent(run, "a1")
        AgentRepository(db).create(failed)
        self._fail_agent(db, run, failed)
        found = asyncio.run(self._store(db).first_failed_wait_ref(
            run.run_id, TENANT, ("a1",),
        ))
        assert found == {
            "agent_id": failed.agent_id, "name": "a1",
            "attempts": 3, "error": "boom",
        }

    def test_slice_lost_failed_agent_never_blocks_the_ref(self, db):
        run = _mk_run(goal="predicate-slice-lost")
        RunRepository(db).create(run)
        failed = _mk_agent(run, "a1")
        AgentRepository(db).create(failed)
        self._fail_agent(db, run, failed, error="slice_lost")
        # A slice_lost FAILED row is a reclaimed claim, not a dead branch:
        # the rescheduled slice respawns the agent under its D16 identity
        # (see rehydrate), so the ref stays satisfiable and never blocks.
        assert asyncio.run(self._store(db).first_failed_wait_ref(
            run.run_id, TENANT, ("a1",),
        )) is None

    def test_slice_lost_resultless_agent_is_the_reschedule_signal(self, db):
        run = _mk_run(goal="predicate-slice-lost-signal")
        RunRepository(db).create(run)
        failed = _mk_agent(run, "a1")
        AgentRepository(db).create(failed)
        self._fail_agent(db, run, failed, error="slice_lost")
        store = self._store(db)
        # The inverse predicate: first_failed_wait_ref stays quiet on a
        # slice_lost row (revivable), while first_slice_lost_wait_ref fires
        # on exactly that row — the in-slice deadlock signal.
        assert asyncio.run(store.first_failed_wait_ref(
            run.run_id, TENANT, ("a1",),
        )) is None
        assert asyncio.run(store.first_slice_lost_wait_ref(
            run.run_id, TENANT, ("a1",),
        )) == {
            "agent_id": failed.agent_id, "name": "a1",
            "attempts": 3, "error": "slice_lost",
        }

    def test_slice_lost_agent_with_result_is_not_the_signal(self, db):
        run = _mk_run(goal="predicate-slice-lost-result")
        RunRepository(db).create(run)
        failed = _mk_agent(run, "a1")
        AgentRepository(db).create(failed)
        self._fail_agent(db, run, failed, error="slice_lost")
        AgentResultRepository(db).create(dm.AgentResult(
            id=dm.ResultId("sh-sl"), run_id=run.run_id, tenant=TENANT,
            source_agent_id=failed.agent_id, payload={"ok": True},
            artifact_key=None, routed_at=dm.EpochMillis(int(time.time() * 1000)),
        ))
        assert asyncio.run(self._store(db).first_slice_lost_wait_ref(
            run.run_id, TENANT, ("a1",),
        )) is None


class TestUnspawnedWaitRefs:
    """A wait ref with no agent row is a deferral, not a dead branch."""

    @pytest.fixture()
    def db(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
        yield _TestDB(engine)
        engine.dispose()

    def _store(self, db) -> SliceStore:
        return SliceStore(
            AgentSwarmConfig(),
            AgentRepository(db), MessageRepository(db),
            AgentResultRepository(db), RoundRepository(db),
            lost_probe=lambda: False,
        )

    def test_refs_without_a_row_are_returned_and_known_refs_excluded(self, db):
        run = _mk_run(goal="unspawned")
        RunRepository(db).create(run)
        spawned = _mk_agent(run, "spawned")
        AgentRepository(db).create(spawned)
        store = self._store(db)

        # The name with a row is known; the deferred name has no row.
        assert asyncio.run(store.unspawned_wait_refs(
            run.run_id, TENANT, ("spawned", "deferred"),
        )) == ("deferred",)

    def test_empty_refs_short_circuits_to_empty(self, db):
        run = _mk_run(goal="unspawned-empty")
        RunRepository(db).create(run)
        assert asyncio.run(self._store(db).unspawned_wait_refs(
            run.run_id, TENANT, (),
        )) == ()


class TestRehydrate:
    """The peer-respawn half: rehydrate revives FAILED slice_lost rows."""

    @pytest.fixture()
    def db(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
        yield _TestDB(engine)
        engine.dispose()

    def _store(self, db, attempt_repo=None) -> SliceStore:
        return SliceStore(
            AgentSwarmConfig(),
            AgentRepository(db), MessageRepository(db),
            AgentResultRepository(db), RoundRepository(db),
            lost_probe=lambda: False,
            attempt_repo=attempt_repo,
        )

    def _claim(self, db, run) -> int:
        dispatch_repo = RunDispatchRepository(db)
        dispatch_repo.enqueue(run_id=run.run_id, tenant=TENANT, deadline_seconds=3600)
        return dispatch_repo.claim_batch(FENCE_WORKER, [TENANT], 1, 0)[0][
            "claim_epoch"]

    def _fail_agent(self, db, run, agent, error: str = "boom") -> int:
        epoch = self._claim(db, run)
        assert AgentRepository(db).update_status(
            run.run_id, agent.agent_id, TENANT, dm.AgentStatus.FAILED.value,
            attempts=3, error=error, claim_epoch=epoch,
        )
        return epoch

    def test_slice_lost_failed_resultless_agent_is_revived_and_reset_to_running(self, db):
        run = _mk_run(goal="rehydrate-slice-lost")
        RunRepository(db).create(run)
        failed = _mk_agent(run, "a1")
        AgentRepository(db).create(failed)
        epoch = self._fail_agent(db, run, failed, error="slice_lost")

        store = self._store(db)
        _, _, interrupted = asyncio.run(store.rehydrate(run, epoch))

        assert [a.agent_id for a in interrupted] == [failed.agent_id]
        stored = AgentRepository(db).get(run.run_id, failed.agent_id, TENANT)
        assert stored.status is dm.AgentStatus.RUNNING
        assert stored.attempts == 0
        assert stored.ended_at is None
        # The row is an in-flight agent for THIS slice's wait: neither
        # the slice_lost sentinel nor the fail-fast predicate may fire
        # on the ref the prologue just respawned.
        assert asyncio.run(store.first_slice_lost_wait_ref(
            run.run_id, TENANT, ("a1",),
        )) is None
        assert asyncio.run(store.first_failed_wait_ref(
            run.run_id, TENANT, ("a1",),
        )) is None

    def test_stale_epoch_reset_is_inert_and_skips_revival(self, db):
        run = _mk_run(goal="rehydrate-stale-fence")
        RunRepository(db).create(run)
        failed = _mk_agent(run, "a1")
        AgentRepository(db).create(failed)
        stale_epoch = self._fail_agent(db, run, failed, error="slice_lost")
        time.sleep(0.02)
        current = RunDispatchRepository(db).reclaim_batch(RECLAIMER, 1, 60)[0][
            "claim_epoch"]
        assert current != stale_epoch

        _, _, interrupted = asyncio.run(self._store(db).rehydrate(
            run, stale_epoch))

        # The current owner revives, not this stale slice: its fenced
        # reset is inert and the agent row stays exactly as the reclaimer
        # will find it.
        assert interrupted == []
        stored = AgentRepository(db).get(run.run_id, failed.agent_id, TENANT)
        assert stored.status is dm.AgentStatus.FAILED
        assert stored.error == "slice_lost"
        assert stored.attempts == 3

    def test_other_error_failed_resultless_agent_stays_terminal(self, db):
        run = _mk_run(goal="rehydrate-boom")
        RunRepository(db).create(run)
        failed = _mk_agent(run, "a1")
        AgentRepository(db).create(failed)
        self._fail_agent(db, run, failed, error="boom")

        _, _, interrupted = asyncio.run(self._store(db).rehydrate(run))

        assert interrupted == []

    def test_revive_cap_stops_respawn_after_burned_budget(self, db):
        # The ledger rows are the DB-recorded retry history: once an agent
        # burned agent_max_attempts provisionings, a later claim's
        # revive must be refused so the failed run terminalizes instead of
        # minting personas forever.
        run = _mk_run(goal="rehydrate-retry-cap")
        RunRepository(db).create(run)
        failed = _mk_agent(run, "a1")
        AgentRepository(db).create(failed)
        epoch = self._fail_agent(db, run, failed, error="slice_lost")

        attempt_repo = AgentAttemptRepository(db)
        for burned in range(AgentSwarmConfig().orchestration.agent_max_attempts):
            assert attempt_repo.record_agent_attempt_intent(
                run.run_id, TENANT, failed.agent_id, epoch, burned + 1,
                dm.AgentKind.BOT.value, f"persona-{burned}", "a1",
            )
        assert attempt_repo.count_agent_provisionings(
            run.run_id, TENANT, failed.agent_id,
        ) == AgentSwarmConfig().orchestration.agent_max_attempts

        store = self._store(db, attempt_repo=attempt_repo)
        _, _, interrupted = asyncio.run(store.rehydrate(run, epoch))

        assert interrupted == []
        stored = AgentRepository(db).get(run.run_id, failed.agent_id, TENANT)
        assert stored.status is dm.AgentStatus.FAILED
        assert stored.error == "slice_lost"

    def test_revive_below_cap_still_respawns(self, db):
        run = _mk_run(goal="rehydrate-retry-below")
        RunRepository(db).create(run)
        failed = _mk_agent(run, "a1")
        AgentRepository(db).create(failed)
        epoch = self._fail_agent(db, run, failed, error="slice_lost")
        attempt_repo = AgentAttemptRepository(db)
        assert attempt_repo.record_agent_attempt_intent(
            run.run_id, TENANT, failed.agent_id, epoch, 1,
            dm.AgentKind.BOT.value, "persona-first", "a1",
        )

        store = self._store(db, attempt_repo=attempt_repo)
        _, _, interrupted = asyncio.run(store.rehydrate(run, epoch))

        assert [a.agent_id for a in interrupted] == [failed.agent_id]

    def test_completed_agent_objective_seeds_the_dedup_set(self, db):
        # An already-COMPLETED row (result delivered) must still contribute
        # its objective to last_spawn_objectives, so the dedup guard blocks
        # a re-spawn of a finished name (the duplicate-row bug fix).
        run = _mk_run(goal="rehydrate-dedup")
        RunRepository(db).create(run)
        done = _mk_agent(run, "w1_everyone")
        AgentRepository(db).create(done)
        AgentResultRepository(db).create(dm.AgentResult(
            id=dm.ResultId("sh-done"), run_id=run.run_id, tenant=TENANT,
            source_agent_id=done.agent_id, payload={"ok": True},
            artifact_key=None, routed_at=dm.EpochMillis(int(time.time() * 1000)),
        ))

        state, _, _ = asyncio.run(self._store(db).rehydrate(run))

        assert done.task_spec.objective in state.last_spawn_objectives