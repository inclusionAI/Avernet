"""SPI contract pins for DefaultSubagentFactory.

The SubagentFactory protocol requires ``agent_id: AgentId`` with no
default: platform idempotency keys derive from it, so a name fallback
would collide same-named spawns (one's teardown could delete the
other's bot). These tests pin the required signature and the identity
derivation through the factory itself.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from contextlib import contextmanager

import pytest
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
from agentclaw.community.core.agentswarm.domain.models import (
    AgentSpec, EpochMillis, ProviderKind, RequestMetadata, Run, RunId,
    RunStatus, TenantId,
)
from agentclaw.community.plugins.community.agentswarm.bot_subagent import (
    AvernetSubagent,
)
from agentclaw.community.plugins.local.agentswarm.factory import (
    DefaultSubagentFactory,
)
from agentclaw.community.plugins.local.agentswarm.llm_stub import StubLLMProvider
from agentclaw.community.plugins.local.agentswarm.subagent_llm import (
    LLMBackedSubagent,
)

_LLM_SPEC = dm.AgentSpec(name="s1", role="research", kind=dm.AgentKind.LLM)
_AVENET_SPEC = dm.AgentSpec(name="scout", role="worker", kind=dm.AgentKind.BOT)


def _mk_run(run_id="run-42") -> Run:
    return Run(
        run_id=RunId(run_id),
        tenant=TenantId("tenant-a"),
        goal="goal",
        agent_catalog=(),
        provider=ProviderKind("avernet"),
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(int(time.time() * 1000)),
        owner_id="factory-owner",
    )


class _RecordingPlatform:
    """Minimal BotPlatform double: records (spec, idempotency_key) and
    answers the seam's optional widening with defaults."""

    def __init__(self) -> None:
        self.created: list[tuple] = []

    def create_bot(self, *, spec, idempotency_key, owner_id=""):
        self.created.append((spec, idempotency_key))
        return "bot-42"

    def bot_status(self, bot_id, owner_id=""):
        return dm.BotState.READY.value

    def chat(self, bot_id, message, owner_id="", session_key=None,
             cancel_event=None):
        return "{}"

    def delete_bot(self, bot_id, owner_id=""):
        return True


class TestFactorySignature:

    def test_inspect_pins_agent_id_as_required(self):
        params = inspect.signature(DefaultSubagentFactory.create).parameters
        assert "agent_id" in params
        assert params["agent_id"].default is inspect.Parameter.empty

    def test_create_without_agent_id_raises_type_error(self):
        factory = DefaultSubagentFactory(
            StubLLMProvider(), platform=_RecordingPlatform())
        run = _mk_run()
        with pytest.raises(TypeError):
            factory.create(_LLM_SPEC, run)
        with pytest.raises(TypeError):
            factory.create(_AVENET_SPEC, run)

    def test_create_with_agent_id_builds_subagents(self):
        factory = DefaultSubagentFactory(
            StubLLMProvider(), platform=_RecordingPlatform())
        run = _mk_run()
        llm_sub = factory.create(_LLM_SPEC, run, dm.AgentId("agent-s1"))
        assert isinstance(llm_sub, LLMBackedSubagent)
        bot_sub = factory.create(
            _AVENET_SPEC, run, dm.AgentId("agent-scout"))
        assert isinstance(bot_sub, AvernetSubagent)

    def test_factory_requires_agent_id_protocol_conformance(self):
        # The runtime_checkable protocol must accept the concrete factory —
        # runtime_checkable checks method presence, not signatures, so the
        # signature pin above is the enforcement point for the contract.
        from agentclaw.community.plugin_api.agentswarm.subagent import (
            SubagentFactory,
        )

        assert isinstance(DefaultSubagentFactory(StubLLMProvider()),
                          SubagentFactory)


class TestSpawnIdentity:

    def test_same_named_bot_spawns_get_distinct_idempotency_keys(self):
        platform = _RecordingPlatform()
        factory = DefaultSubagentFactory(llm=None, platform=platform)
        run = _mk_run()
        spec = dm.AgentSpec(name="scout", role="worker",
                            kind=dm.AgentKind.BOT)

        first = factory.create(spec, run, dm.AgentId("agent-one"))
        second = factory.create(spec, run, dm.AgentId("agent-two"))

        async def _setup_both():
            await first.setup()
            await second.setup()

        asyncio.run(_setup_both())
        keys = [key for _, key in platform.created]
        assert keys == ["run-42:agent-one:1", "run-42:agent-two:1"]

    def test_same_identity_across_run_respawn_rebinds_one_key(self):
        platform = _RecordingPlatform()
        factory = DefaultSubagentFactory(llm=None, platform=platform)
        run = _mk_run()
        spec = dm.AgentSpec(name="scout", role="worker",
                            kind=dm.AgentKind.BOT)

        first = factory.create(spec, run, dm.AgentId("agent-1"))
        asyncio.run(first.setup())
        asyncio.run(first.teardown())

        respawn = factory.create(spec, run, dm.AgentId("agent-1"))
        asyncio.run(respawn.setup())

        keys = [key for _, key in platform.created]
        assert keys == ["run-42:agent-1:1", "run-42:agent-1:1"]


class TestPerAttemptThreading:
    """D-J: the attempt index threads factory.create -> subagent ctors.

    The default is 1 (out-of-swarm callers keep working); the pool's
    retry index is what mints a fresh persona per retry.
    """

    def test_signature_pins_attempt_default_one_and_agent_id_required(self):
        params = inspect.signature(DefaultSubagentFactory.create).parameters
        assert params["agent_id"].default is inspect.Parameter.empty
        assert "attempt" in params
        assert params["attempt"].default == 1

    def test_attempt_defaults_to_one_for_both_kinds(self):
        factory = DefaultSubagentFactory(
            StubLLMProvider(), platform=_RecordingPlatform())
        run = _mk_run()
        bot_sub = factory.create(_AVENET_SPEC, run, dm.AgentId("agent-scout"))
        llm_sub = factory.create(_LLM_SPEC, run, dm.AgentId("agent-s1"))
        assert bot_sub._attempt == 1
        assert llm_sub._attempt == 1

    def test_attempt_threads_to_both_kinds(self):
        factory = DefaultSubagentFactory(
            StubLLMProvider(), platform=_RecordingPlatform())
        run = _mk_run()
        bot_sub = factory.create(
            _AVENET_SPEC, run, dm.AgentId("agent-scout"), 2)
        llm_sub = factory.create(
            _LLM_SPEC, run, dm.AgentId("agent-s1"), 3)
        assert bot_sub._attempt == 2
        assert llm_sub._attempt == 3

    def test_both_kinds_conform_to_the_subagent_protocol(self):
        from agentclaw.community.plugin_api.agentswarm.subagent import (
            Subagent,
        )

        factory = DefaultSubagentFactory(
            StubLLMProvider(), platform=_RecordingPlatform())
        run = _mk_run()
        bot_sub = factory.create(_AVENET_SPEC, run, dm.AgentId("agent-scout"))
        llm_sub = factory.create(_LLM_SPEC, run, dm.AgentId("agent-s1"))
        assert isinstance(bot_sub, Subagent)
        assert isinstance(llm_sub, Subagent)
        # The additive executor surfaces are part of the contract now:
        # both kinds carry them pre-setup (empty/identity by convention).
        assert hasattr(bot_sub, "executor_id")
        assert hasattr(bot_sub, "executor_name")
        assert llm_sub.executor_id
        assert llm_sub.executor_name.startswith("s1_")

    def test_llm_kind_exposes_the_bare_per_attempt_digest(self):
        factory = DefaultSubagentFactory(StubLLMProvider())
        run = _mk_run()
        sub = factory.create(_LLM_SPEC, run, dm.AgentId("agent-s1"), 2)
        digest16 = dm.spawn_digest("run-42", "agent-s1", 2, 16)
        digest12 = dm.spawn_digest("run-42", "agent-s1", 2, 12)
        assert sub.executor_id == digest16
        assert not sub.executor_id.startswith("agent_swarm_")
        assert sub.executor_name == f"s1_{digest12}"


class _DeletionPlatform:
    """BotPlatform double for the destroy sweep: records delete calls,
    with an optional per-id scripted outcome (False = already gone,
    Exception = failure)."""

    def __init__(self, outcomes: dict | None = None) -> None:
        self.deleted: list[str] = []
        self.outcomes = outcomes or {}

    def create_bot(self, *, spec, idempotency_key, owner_id=""):
        return "bot-42"

    def bot_status(self, bot_id, owner_id=""):
        return dm.BotState.READY.value

    def chat(self, bot_id, message, owner_id="", session_key=None,
             cancel_event=None):
        return "{}"

    def delete_bot(self, bot_id, owner_id=""):
        self.deleted.append(str(bot_id))
        outcome = self.outcomes.get(str(bot_id))
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            return True
        return outcome


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


def _seed_run_and_claim(db, run_id) -> int:
    RunRepository(db).create(dm.Run(
        run_id=dm.RunId(run_id), tenant=TenantId("tenant-a"),
        goal="ledger", agent_catalog=(), provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(int(time.time() * 1000)),
    ))
    dispatch_repo = RunDispatchRepository(db)
    dispatch_repo.enqueue(run_id=dm.RunId(run_id), tenant=TenantId("tenant-a"),
                     deadline_seconds=3600)
    return dispatch_repo.claim_batch(
        "sweep-worker", [TenantId("tenant-a")], 1, 0,
    )[0]["claim_epoch"]


def _journal(db, run_id, agent_id, claim_epoch, attempt, kind, executor_id,
             executor_name) -> bool:
    return AgentAttemptRepository(db).record_agent_binding(
        dm.RunId(run_id), TenantId("tenant-a"), dm.AgentId(agent_id),
        claim_epoch, attempt, kind, executor_id, executor_name,
    )


def _mk_sweep_agent(name, agent_id, bot_id=None,
                    kind=dm.AgentKind.BOT) -> dm.Agent:
    return dm.Agent(
        agent_id=dm.AgentId(agent_id),
        run_id=dm.RunId("run-sweep"),
        tenant=TenantId("tenant-a"),
        parent_agent_id=None, depth=0, name=name, role="work", kind=kind,
        task_spec=dm.TaskEnvelope(
            name=name, objective="work", output_schema={}, boundaries="",
            parent_goal="g",
        ),
        spawn_round=0, status=dm.AgentStatus.KILLED, attempts=1,
        idempotency_key=dm.IdempotencyKey("run-sweep:" + agent_id),
        output={}, bot_id=dm.BotId(bot_id) if bot_id else None,
    )


class TestDestroyRunAgentsBotsSweep:
    """D-M: ledger-first terminal sweep + the legacy derivation belt.

    Runs against the real AgentAttemptRepository (rows seeded through
    the fenced insert) and a recording platform double.
    """

    def _factory(self, db, platform):
        return DefaultSubagentFactory(
            llm=None, platform=platform,
            attempt_repo=AgentAttemptRepository(db),
        )

    def _rows(self, db):
        return AgentAttemptRepository(db).list_agent_bindings(
            dm.RunId("run-sweep"), TenantId("tenant-a"))

    def test_multi_attempt_multi_slice_rows_all_destroyed_and_marked(self):
        db = _sqlite_db()
        epoch = _seed_run_and_claim(db, "run-sweep")
        assert _journal(db, "run-sweep", "agent-1", epoch, 1,
                        dm.AgentKind.BOT.value, "ac_attempt1", "scout-a1")
        assert _journal(db, "run-sweep", "agent-1", epoch, 2,
                        dm.AgentKind.BOT.value, "ac_attempt2", "scout-a2")
        time.sleep(0.02)
        later = RunDispatchRepository(db).reclaim_batch(
            "sweep-worker-2", 1, 60)[0]["claim_epoch"]
        assert _journal(db, "run-sweep", "agent-1", later, 1,
                        dm.AgentKind.BOT.value, "ac_replayed", "scout-a3")
        platform = _DeletionPlatform()
        destroyed = self._factory(db, platform).destroy_run_agents_bots(
            _mk_run("run-sweep"), [])
        assert sorted(destroyed) == [
            "ac_attempt1", "ac_attempt2", "ac_replayed"]
        assert {r["binding_state"] for r in self._rows(db)} == {"DESTROYED"}

    def test_cross_slice_adopt_replay_dedupes_delete_but_marks_both(self):
        db = _sqlite_db()
        epoch = _seed_run_and_claim(db, "run-sweep")
        assert _journal(db, "run-sweep", "agent-1", epoch, 1,
                        dm.AgentKind.BOT.value, "ac_adopted", "scout-a")
        time.sleep(0.02)
        replay_epoch = RunDispatchRepository(db).reclaim_batch(
            "sweep-worker-2", 1, 60)[0]["claim_epoch"]
        # Crash-resume adopt shape: the reclaimer replays the SAME
        # attempt number and re-derives the SAME persona (adopt), then
        # journals it under ITS OWN epoch — one executor id, two rows.
        assert _journal(db, "run-sweep", "agent-1", replay_epoch, 1,
                        dm.AgentKind.BOT.value, "ac_adopted", "scout-a")
        platform = _DeletionPlatform()
        destroyed = self._factory(db, platform).destroy_run_agents_bots(
            _mk_run("run-sweep"), [])
        assert platform.deleted == ["ac_adopted"]
        assert destroyed == ["ac_adopted"]
        rows = self._rows(db)
        assert len(rows) == 2 and all(
            r["binding_state"] == "DESTROYED" for r in rows)

    def test_llm_kind_rows_settle_ledger_without_platform_calls(self):
        db = _sqlite_db()
        epoch = _seed_run_and_claim(db, "run-sweep")
        assert _journal(db, "run-sweep", "agent-llm", epoch, 1,
                        dm.AgentKind.LLM.value, "123456789abcdef01", "clerk-1")
        platform = _DeletionPlatform()
        destroyed = self._factory(db, platform).destroy_run_agents_bots(
            _mk_run("run-sweep"), [])
        # Nothing platform-backed exists for an llm-kind row: the sweep
        # settles the ledger (churn fix — an unsettled row keeps
        # _has_undestroyed_ledger True and the reaper re-visiting the
        # terminal run forever) without a single platform call.
        assert destroyed == []
        assert platform.deleted == []
        (row,) = self._rows(db)
        assert row["binding_state"] == "DESTROYED"

    def test_blank_llm_intent_rows_settle_by_correlation(self):
        db = _sqlite_db()
        epoch = _seed_run_and_claim(db, "run-sweep")
        assert AgentAttemptRepository(db).record_agent_attempt_intent(
            dm.RunId("run-sweep"), TenantId("tenant-a"),
            dm.AgentId("agent-llm"), epoch, 1,
            dm.AgentKind.LLM.value, "", "clerk-intent") is True
        platform = _DeletionPlatform()
        destroyed = self._factory(db, platform).destroy_run_agents_bots(
            _mk_run("run-sweep"), [])
        assert destroyed == []
        assert platform.deleted == []
        (row,) = self._rows(db)
        assert row["binding_state"] == "DESTROYED"

    def test_legacy_belt_destroys_derived_and_recorded_ids(self):
        from agentclaw.community.plugins.community.agentswarm.host import (
            bot_id_for,
        )

        db = _sqlite_db()
        _seed_run_and_claim(db, "run-sweep")
        platform = _DeletionPlatform()
        agents = [
            _mk_sweep_agent("scout", "agent-1", bot_id=None),
            _mk_sweep_agent("clerk", "agent-2", bot_id="ac_kept"),
            _mk_sweep_agent("s1", "agent-3", kind=dm.AgentKind.LLM),
        ]
        destroyed = self._factory(db, platform).destroy_run_agents_bots(
            _mk_run("run-sweep"), agents)
        assert sorted(destroyed) == sorted([
            bot_id_for("run-sweep", "agent-1"), "ac_kept",
        ])
        assert sorted(platform.deleted) == sorted(destroyed)

    def test_gone_and_failing_deletes_do_not_block_the_rest(self):
        db = _sqlite_db()
        epoch = _seed_run_and_claim(db, "run-sweep")
        for agent_id, attempt, executor_id in (
            ("agent-1", 1, "ac_gone"),
            ("agent-1", 2, "ac_boom"),
            ("agent-2", 1, "ac_ok"),
        ):
            assert _journal(db, "run-sweep", agent_id, epoch, attempt,
                            dm.AgentKind.BOT.value, executor_id, "scout")
        platform = _DeletionPlatform(outcomes={
            "ac_gone": False,
            "ac_boom": RuntimeError("platform hiccup"),
        })
        destroyed = self._factory(db, platform).destroy_run_agents_bots(
            _mk_run("run-sweep"), [])
        # quiet-if-gone: failed/absent ids never block the ok one; rows
        # are marked only after destroy success, so only ac_ok flips.
        assert destroyed == ["ac_ok"]
        rows = {r["executor_id"]: r for r in self._rows(db)}
        assert rows["ac_ok"]["binding_state"] == "DESTROYED"
        assert rows["ac_boom"]["binding_state"] == "SPAWNED"
        assert rows["ac_gone"]["binding_state"] == "SPAWNED"

    def test_without_attempt_repo_the_belt_still_runs(self):
        from agentclaw.community.plugins.community.agentswarm.host import (
            bot_id_for,
        )

        db = _sqlite_db()
        epoch = _seed_run_and_claim(db, "run-sweep")
        assert _journal(db, "run-sweep", "agent-1", epoch, 1,
                        dm.AgentKind.BOT.value, "ac_ledger", "scout-l")
        platform = _DeletionPlatform()
        factory = DefaultSubagentFactory(llm=None, platform=platform)
        destroyed = factory.destroy_run_agents_bots(
            _mk_run("run-sweep"), [_mk_sweep_agent("scout", "agent-1")])
        assert destroyed == [bot_id_for("run-sweep", "agent-1")]
        assert "ac_ledger" not in platform.deleted


def _derived_intent_id(run_id, agent_id, attempt):
    return "agent_swarm_" + dm.spawn_digest(
        str(run_id), str(agent_id), int(attempt), 16)


def _journal_intent(db, run_id, agent_id, epoch, attempt):
    assert AgentAttemptRepository(db).record_agent_attempt_intent(
        dm.RunId(run_id), TenantId("tenant-a"), dm.AgentId(agent_id),
        epoch, attempt, dm.AgentKind.BOT.value, "", "scout") is True


def _backdate_started_at(db, run_id, agent_id, started_at_ms):
    from sqlalchemy import update

    from agentclaw.community.core.agentswarm.repository.agent_attempt import (
        model as om_attempt,
    )
    with db.orm_session() as session:
        session.execute(
            update(om_attempt.AcAgentSwarmAgentAttemptModel).where(
                om_attempt.AcAgentSwarmAgentAttemptModel.run_id == run_id,
                om_attempt.AcAgentSwarmAgentAttemptModel.tenant
                == TenantId("tenant-a"),
                om_attempt.AcAgentSwarmAgentAttemptModel.agent_id == agent_id,
            ).values(started_at=started_at_ms)
        )


class _MaterializationProbePlatform(_DeletionPlatform):
    """BotPlatform double whose bot_status answers a scripted per-id
    state (PROVISIONING by default), and which records every status
    probe so the deferral path proves itself by absence: a deferred row
    issues a probe and never reaches delete_bot."""

    def __init__(self, statuses: dict | None = None) -> None:
        super().__init__()
        self.statuses = statuses or {}
        self.status_probes: list[str] = []

    def bot_status(self, bot_id, owner_id=""):
        self.status_probes.append(str(bot_id))
        outcome = self.statuses.get(str(bot_id),
                                    dm.BotState.PROVISIONING.value)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestBlankIntentMaterializationGrace:
    """A bot-kind persona's bootstrap is a DURABLE async job: the blank
    intent row is journaled at spawn and the persona materializes
    seconds to minutes later (task-queue retry cadence). A cancel-time
    sweep must not mark the row DESTROYED before materialization —
    a live-proven leak of ac_bot + ACTIVE BaaS device + held quota —
    so unmaterialized personas inside the grace window defer to the
    reaper's next cycle, and past it settle cleanly instead of
    churning the reaper forever."""

    def _factory(self, db, platform):
        return DefaultSubagentFactory(
            llm=None, platform=platform,
            attempt_repo=AgentAttemptRepository(db),
        )

    def _rows(self, db):
        return AgentAttemptRepository(db).list_agent_bindings(
            dm.RunId("run-sweep"), TenantId("tenant-a"))

    def test_unmaterialized_within_grace_defers_no_delete_no_marks(self):
        db = _sqlite_db()
        epoch = _seed_run_and_claim(db, "run-sweep")
        _journal_intent(db, "run-sweep", "agent-1", epoch, 1)
        derived = _derived_intent_id("run-sweep", "agent-1", 1)

        platform = _MaterializationProbePlatform()
        destroyed = self._factory(db, platform).destroy_run_agents_bots(
            _mk_run("run-sweep"), [])

        # Deferred wholesale: one status probe, no delete call, no
        # marks — the reaper's next cycle re-attempts the row.
        assert destroyed == []
        assert platform.status_probes == [derived]
        assert platform.deleted == []
        (row,) = self._rows(db)
        assert row["binding_state"] == "SPAWNED"

    def test_unmaterialized_probe_failure_defers_like_provisioning(self):
        db = _sqlite_db()
        epoch = _seed_run_and_claim(db, "run-sweep")
        _journal_intent(db, "run-sweep", "agent-1", epoch, 1)
        derived = _derived_intent_id("run-sweep", "agent-1", 1)

        platform = _MaterializationProbePlatform(statuses={
            derived: RuntimeError("platform unreachable"),
        })
        destroyed = self._factory(db, platform).destroy_run_agents_bots(
            _mk_run("run-sweep"), [])

        assert destroyed == []
        assert platform.status_probes == [derived]
        assert platform.deleted == []
        (row,) = self._rows(db)
        assert row["binding_state"] == "SPAWNED"

    def test_unmaterialized_past_grace_gives_up_and_settles(self):
        db = _sqlite_db()
        epoch = _seed_run_and_claim(db, "run-sweep")
        _journal_intent(db, "run-sweep", "agent-1", epoch, 1)
        derived = _derived_intent_id("run-sweep", "agent-1", 1)
        # Past the give-up horizon (2 x orchestration.setup_timeout,
        # default 300s): the default factory grace expired long ago.
        _backdate_started_at(
            db, "run-sweep", dm.AgentId("agent-1"),
            int(time.time() * 1000) - 3 * 600 * 1000,
        )

        platform = _MaterializationProbePlatform()
        destroyed = self._factory(db, platform).destroy_run_agents_bots(
            _mk_run("run-sweep"), [])

        # Settle cleanly: the quiet-if-gone delete is attempted once
        # (harmless when nothing materialized) and BOTH marks settle
        # the row so it can never churn the reaper forever.
        assert destroyed == []
        assert platform.status_probes == [derived]
        assert platform.deleted == [derived]
        (row,) = self._rows(db)
        assert row["binding_state"] == "DESTROYED"

    def test_materialized_by_status_destroyed_as_today(self):
        db = _sqlite_db()
        epoch = _seed_run_and_claim(db, "run-sweep")
        _journal_intent(db, "run-sweep", "agent-1", epoch, 1)
        derived = _derived_intent_id("run-sweep", "agent-1", 1)

        platform = _MaterializationProbePlatform(statuses={
            derived: dm.BotState.READY.value,
        })
        destroyed = self._factory(db, platform).destroy_run_agents_bots(
            _mk_run("run-sweep"), [])

        assert destroyed == [derived]
        assert platform.status_probes == [derived]
        assert platform.deleted == [derived]
        (row,) = self._rows(db)
        assert row["binding_state"] == "DESTROYED"

    def test_settled_executor_rows_bypass_the_grace_probe(self):
        # Regression pin: the materialization gate exists ONLY for
        # blank-intent rows; a settled executor_id row goes straight
        # to the delete as before, whatever bot_status would answer.
        db = _sqlite_db()
        epoch = _seed_run_and_claim(db, "run-sweep")
        assert _journal(db, "run-sweep", "agent-1", epoch, 1,
                        dm.AgentKind.BOT.value, "ac_settled", "scout")

        platform = _MaterializationProbePlatform()
        destroyed = self._factory(db, platform).destroy_run_agents_bots(
            _mk_run("run-sweep"), [])

        assert destroyed == ["ac_settled"]
        assert platform.status_probes == []
        assert platform.deleted == ["ac_settled"]
        (row,) = self._rows(db)
        assert row["binding_state"] == "DESTROYED"

    def test_setup_timeout_threads_to_the_grace_horizon(self):
        # The horizon is row.started_at + 2 x setup_timeout: a tiny
        # configured budget makes even a fresh intent row past-grace,
        # which is the configured-knob path the DI root wires.
        db = _sqlite_db()
        epoch = _seed_run_and_claim(db, "run-sweep")
        _journal_intent(db, "run-sweep", "agent-1", epoch, 1)
        derived = _derived_intent_id("run-sweep", "agent-1", 1)

        platform = _MaterializationProbePlatform()
        factory = DefaultSubagentFactory(
            llm=None, platform=platform,
            attempt_repo=AgentAttemptRepository(db),
            setup_timeout=0.0,
        )
        destroyed = factory.destroy_run_agents_bots(_mk_run("run-sweep"), [])

        assert destroyed == []
        assert platform.deleted == [derived]
        (row,) = self._rows(db)
        assert row["binding_state"] == "DESTROYED"