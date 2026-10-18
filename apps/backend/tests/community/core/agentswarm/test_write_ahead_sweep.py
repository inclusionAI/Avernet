"""Terminal destroy sweep over write-ahead intent rows.

A blank-executor ledger row (the write-ahead intent an agent's failed
setup left behind) is still reconciled: the sweep derives the
with-attempt persona identity from the row's own correlation columns,
deletes it on the platform, and marks the row DESTROYED by
``mark_intent_binding_destroyed`` while settled rows keep the
executor_id-level path.
"""
from __future__ import annotations

import time
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.agent_attempt import (
    AgentAttemptRepository,
)
from agentclaw.community.core.agentswarm.repository.run_dispatch import RunDispatchRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.plugins.local.agentswarm.factory import (
    DefaultSubagentFactory,
)


def _sqlite_db():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    factory = sessionmaker(bind=engine)

    class _DB:
        @contextmanager
        def orm_session(self):
            db = factory()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    db = _DB()
    db.engine = engine
    return db


class _DeletionPlatform:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def create_bot(self, *, spec, idempotency_key, owner_id=""):
        return "bot-42"

    def bot_status(self, bot_id, owner_id=""):
        return dm.BotState.READY.value

    def chat(self, bot_id, message, owner_id="", session_key=None,
             cancel_event=None):
        return "{}"

    def delete_bot(self, bot_id, owner_id=""):
        self.deleted.append(str(bot_id))
        return True


def _run_row(run_id="run-sweep"):
    return dm.Run(
        run_id=dm.RunId(run_id), tenant=dm.TenantId("tenant-a"),
        goal="sweep goal", agent_catalog=(), provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(), status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(int(time.time() * 1000)),
        owner_id="sweep-owner",
    )


def _seed(db, run) -> int:
    RunRepository(db).create(run)
    dispatch_repo = RunDispatchRepository(db)
    dispatch_repo.enqueue(run_id=run.run_id, tenant=run.tenant,
                      deadline_seconds=3600)
    return dispatch_repo.claim_batch(
        "sweep-worker", [run.tenant], 1, 0)[0]["claim_epoch"]


def _intent(db, run, agent_id, epoch, attempt):
    assert AgentAttemptRepository(db).record_agent_attempt_intent(
        run.run_id, run.tenant, dm.AgentId(agent_id), epoch, attempt,
        dm.AgentKind.BOT.value, "", "scout") is True


class TestSweepReconcilesWriteAheadIntentRows:

    def test_blank_intent_rows_get_derived_delete_and_marked_destroyed(self):
        db = _sqlite_db()
        run = _run_row()
        epoch = _seed(db, run)
        _intent(db, run, "agent-1", epoch, 1)
        _intent(db, run, "agent-1", epoch, 2)

        platform = _DeletionPlatform()
        factory = DefaultSubagentFactory(
            llm=None, platform=platform,
            attempt_repo=AgentAttemptRepository(db),
        )
        destroyed = factory.destroy_run_agents_bots(run, [])

        # The sweep derived with-attempt persona ids from each row's own
        # (run, agent, attempt) correlation and deleted every one.
        expected = [
            "agent_swarm_" + dm.spawn_digest(str(run.run_id), "agent-1", 1, 16),
            "agent_swarm_" + dm.spawn_digest(str(run.run_id), "agent-1", 2, 16),
        ]
        assert sorted(destroyed) == sorted(expected)
        assert sorted(platform.deleted) == sorted(expected)
        rows = AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, run.tenant)
        assert len(rows) == 2
        assert all(r["binding_state"] == "DESTROYED" for r in rows)
        # The blank id itself never leaked into the platform surface.
        assert "" not in platform.deleted

    def test_settled_and_blank_rows_of_one_run_reconcile_together(self):
        db = _sqlite_db()
        run = _run_row()
        epoch = _seed(db, run)
        repo = AgentAttemptRepository(db)
        _intent(db, run, "agent-1", epoch, 1)
        assert repo.record_agent_binding(
            run.run_id, run.tenant, dm.AgentId("agent-2"), epoch, 1,
            dm.AgentKind.BOT.value, "ac_settled", "scout-settled") is True

        platform = _DeletionPlatform()
        factory = DefaultSubagentFactory(
            llm=None, platform=platform,
            attempt_repo=AgentAttemptRepository(db),
        )
        destroyed = factory.destroy_run_agents_bots(run, [])

        assert sorted(destroyed) == sorted([
            "ac_settled",
            "agent_swarm_" + dm.spawn_digest(str(run.run_id), "agent-1", 1, 16),
        ])
        rows = AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, run.tenant)
        assert all(r["binding_state"] == "DESTROYED" for r in rows)
        by_agent = {r["agent_id"]: r["executor_id"] for r in rows}
        assert by_agent["agent-1"] == ""
        assert by_agent["agent-2"] == "ac_settled"


class _ProvisioningThenMaterializedPlatform(_DeletionPlatform):
    """Status double for the cancel-before-materialize leak scenario:
    PROVISIONING on the first sweep, READY after the persona lands."""

    def __init__(self) -> None:
        super().__init__()
        self.materialized = False
        self.status_probes: list[str] = []

    def bot_status(self, bot_id, owner_id=""):
        self.status_probes.append(str(bot_id))
        if self.materialized:
            return dm.BotState.READY.value
        return dm.BotState.PROVISIONING.value


class TestSweepDefersUntilMaterialization:
    """The live-proven leak: a cancel mid-spawn sweeps the blank intent
    row BEFORE the durable creation job materializes the persona. The
    grace window defers the row wholesale, and the reaper's NEXT cycle
    — the retry vehicle — destroys the persona once it landed."""

    def test_deferred_row_settles_on_the_next_sweep_after_materialization(
            self):
        db = _sqlite_db()
        run = _run_row()
        epoch = _seed(db, run)
        _intent(db, run, "agent-1", epoch, 1)
        derived = "agent_swarm_" + dm.spawn_digest(
            str(run.run_id), "agent-1", 1, 16)

        platform = _ProvisioningThenMaterializedPlatform()
        factory = DefaultSubagentFactory(
            llm=None, platform=platform,
            attempt_repo=AgentAttemptRepository(db),
        )

        # Cancel-time sweep (persona not yet materialized, inside the
        # grace window): no delete, no marks — otherwise the persona
        # that lands seconds later is orphaned forever.
        destroyed = factory.destroy_run_agents_bots(run, [])
        assert destroyed == []
        assert platform.status_probes == [derived]
        assert platform.deleted == []
        (row,) = AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, run.tenant)
        assert row["binding_state"] == "SPAWNED"

        # The persona materializes (task-queue completes) and the
        # reaper's next cycle re-attempts the row: destroy + settle.
        platform.materialized = True
        destroyed = factory.destroy_run_agents_bots(run, [])
        assert destroyed == [derived]
        assert platform.status_probes == [derived, derived]
        assert platform.deleted == [derived]
        (row,) = AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, run.tenant)
        assert row["binding_state"] == "DESTROYED"