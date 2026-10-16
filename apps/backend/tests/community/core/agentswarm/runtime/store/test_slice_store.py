"""SliceStore claim-fence tests (D28): persist_completion is slice-owned.

A fenced-off update_status (claim lost to a reclaimer) must persist
nothing further and unwind as the handler's lost-claim signal, so the
shard/message exactly-once convergence (D24) stays with the new owner.

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
from agentclaw.community.core.agentswarm.repository.job import JobRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.repository.shard import ResultShardRepository
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
            ResultShardRepository(db), RoundRepository(db),
            lost_probe=lambda: False,
        )

    def _claim(self, db, run) -> int:
        job_repo = JobRepository(db)
        job_repo.enqueue(run_id=run.run_id, tenant=run.tenant,
                         deadline_seconds=3600)
        return job_repo.claim_batch(
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
        # the reclaimer takes the job over between renewal ticks
        time.sleep(0.02)
        reclaims = JobRepository(db).reclaim_batch(RECLAIMER, 1, 60)
        assert [r["run_id"] for r in reclaims] == [run.run_id]

        store = self._store(db)
        with pytest.raises(_SliceLost):
            asyncio.run(store.persist_completion(
                self._completion(run, agent.agent_id), claim_epoch=lost_epoch,
            ))

        stored = AgentRepository(db).get(run.run_id, agent.agent_id, TENANT)
        assert stored.status is dm.AgentStatus.QUEUED
        assert ResultShardRepository(db).get_by_run_agent(
            run.run_id, agent.agent_id, TENANT,
        ) is None
        assert MessageRepository(db).list_by_agent(
            run.run_id, agent.agent_id, TENANT,
        ) == []

    def test_matching_epoch_persists_shard_and_message(self, db):
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

        shard = ResultShardRepository(db).get_by_run_agent(
            run.run_id, agent.agent_id, TENANT,
        )
        assert shard is not None and shard.payload == {"r": 1}
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
            ResultShardRepository(db), RoundRepository(db),
            lost_probe=lambda: False,
            attempt_repo=AgentAttemptRepository(db),
        )

    def _claim(self, db, run) -> int:
        job_repo = JobRepository(db)
        job_repo.enqueue(run_id=run.run_id, tenant=run.tenant,
                         deadline_seconds=3600)
        return job_repo.claim_batch(FENCE_WORKER, [run.tenant], 1, 0)[0][
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
        assert row["attempt"] == 2
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
        JobRepository(db).reclaim_batch(RECLAIMER, 1, 60)

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
            ResultShardRepository(db), RoundRepository(db),
            lost_probe=lambda: False,
        ).record_agent_binding(
            run, dm.AgentId("agent-x"), epoch, attempt=1,
            executor_kind=dm.AgentKind.BOT.value,
            executor_id="ac_personaid", executor_name="scout",
        ))
        assert AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT) == []