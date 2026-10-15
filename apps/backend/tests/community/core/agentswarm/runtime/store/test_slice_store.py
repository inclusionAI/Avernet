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