"""Storage integration tests: every repository exercised against a real
SqliteDB DatabasePlugin (D19, D31, D32, tenant stamping, logical delete)."""
from __future__ import annotations

import time
import uuid

import pytest
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.repository import registry as om
from agentclaw.community.core.agentcompute.repository.agent import AgentRepository
from agentclaw.community.core.agentcompute.repository.guard import GuardEventRepository
from agentclaw.community.core.agentcompute.repository.job import JobRepository
from agentclaw.community.core.agentcompute.repository.message import MessageRepository
from agentclaw.community.core.agentcompute.repository.shard import ResultShardRepository
from agentclaw.community.core.agentcompute.repository.round import RoundRepository
from agentclaw.community.core.agentcompute.repository.run import RunRepository


class _TestDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    session = orm_session


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        om.AcAgentComputeRunModel.__table__,
        om.AcAgentComputeAgentModel.__table__,
        om.AcAgentComputeMessageModel.__table__,
        om.AcAgentComputeResultShardModel.__table__,
        om.AcAgentComputeRoundModel.__table__,
        om.AcAgentComputeGuardEventModel.__table__,
        om.AcAgentComputeJobModel.__table__,
    ])
    yield _TestDB(engine)
    engine.dispose()


@pytest.fixture()
def run_repo(db):
    return RunRepository(db)


@pytest.fixture()
def agent_repo(db):
    return AgentRepository(db)


@pytest.fixture()
def msg_repo(db):
    return MessageRepository(db)


@pytest.fixture()
def shard_repo(db):
    return ResultShardRepository(db)


@pytest.fixture()
def round_repo(db):
    return RoundRepository(db)


@pytest.fixture()
def guard_repo(db):
    return GuardEventRepository(db)


@pytest.fixture()
def job_repo(db):
    return JobRepository(db)


TENANT_A = dm.TenantId("tenant-a")
TENANT_B = dm.TenantId("tenant-b")


def _now_ms():
    return int(time.time() * 1000)


def _mk_run(run_id=None, tenant=TENANT_A) -> dm.Run:
    return dm.Run(
        run_id=dm.RunId(run_id or uuid.uuid4().hex),
        tenant=tenant,
        goal="test goal",
        agent_catalog=(
            dm.AgentSpec(name="r1", role="research"),
        ),
        provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(_now_ms()),
    )


def _mk_agent(run: dm.Run, name="a1") -> dm.Agent:
    env = dm.TaskEnvelope(
        name=name, objective="do", output_schema={},
        boundaries="", parent_goal=run.goal,
    )
    return dm.Agent(
        agent_id=dm.AgentId(uuid.uuid4().hex),
        run_id=run.run_id,
        tenant=TENANT_A,
        parent_agent_id=None,
        depth=0,
        name=name,
        role="role",
        kind=dm.AgentKind.LLM,
        task_spec=env,
        spawn_round=0,
        status=dm.AgentStatus.QUEUED,
        attempts=0,
        idempotency_key=dm.IdempotencyKey(uuid.uuid4().hex),
        output={},
    )


def _mk_shard(run: dm.Run, agent: dm.Agent) -> dm.ResultShard:
    return dm.ResultShard(
        id=dm.ShardId(uuid.uuid4().hex),
        run_id=run.run_id,
        tenant=TENANT_A,
        source_agent_id=agent.agent_id,
        payload={"result": "done"},
        artifact_key=None,
        routed_at=dm.EpochMillis(_now_ms()),
    )


class TestRunRepository:
    def test_create_and_get(self, run_repo):
        run = _mk_run()
        run_repo.create(run)
        fetched = run_repo.get(run.run_id, TENANT_A)
        assert fetched is not None
        assert fetched.run_id == run.run_id
        assert fetched.tenant == TENANT_A
        assert fetched.goal == "test goal"

    def test_catalog_kind_round_trips_as_enum(self, run_repo):
        run = _mk_run()
        run.agent_catalog = (
            dm.AgentSpec(name="r1", role="research"),
            dm.AgentSpec(name="r2", role="bot", kind=dm.AgentKind.AVENET),
        )
        run_repo.create(run)
        fetched = run_repo.get(run.run_id, TENANT_A)
        kinds = [spec.kind for spec in fetched.agent_catalog]
        assert all(isinstance(kind, dm.AgentKind) for kind in kinds)
        assert [kind.value for kind in kinds] == ["llm", "avernet"]

    def test_tenant_isolation(self, run_repo):
        run = _mk_run(tenant=TENANT_A)
        run_repo.create(run)
        assert run_repo.get(run.run_id, TENANT_B) is None
        assert run_repo.get(run.run_id, TENANT_A) is not None

    def test_update_status(self, run_repo, job_repo):
        run = _mk_run()
        run_repo.create(run)
        job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        claimed = job_repo.claim_batch("w1", None, 1, 60)[0]
        ok = run_repo.update_status(
            run.run_id, TENANT_A,
            dm.RunStatus.COMPLETED,
            termination_reason=dm.TerminationReason.FINISH,
            final_answer="answer",
            claim_epoch=claimed["claim_epoch"],
        )
        assert ok
        fetched = run_repo.get(run.run_id, TENANT_A)
        assert fetched.status == dm.RunStatus.COMPLETED
        assert fetched.ended_at is not None

    def test_get_by_idempotency_running_only(self, run_repo, job_repo):
        run = _mk_run()
        run.active_idempotency_key = dm.IdempotencyKey("idem-key-1")
        run_repo.create(run)
        assert run_repo.get_by_idempotency("idem-key-1", TENANT_A) is not None
        job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        claimed = job_repo.claim_batch("w1", None, 1, 60)[0]
        assert run_repo.update_status(
            run.run_id, TENANT_A, dm.RunStatus.COMPLETED,
            claim_epoch=claimed["claim_epoch"],
        )
        assert run_repo.get_by_idempotency("idem-key-1", TENANT_A) is None

    def test_idempotency_tenant_isolated(self, run_repo):
        run = _mk_run(tenant=TENANT_A)
        run.active_idempotency_key = dm.IdempotencyKey("idem-2")
        run_repo.create(run)
        assert run_repo.get_by_idempotency("idem-2", TENANT_B) is None

    def test_get_nonexistent(self, run_repo):
        assert run_repo.get("nope", TENANT_A) is None

    def test_update_status_fenced_by_claim_epoch(self, run_repo, job_repo):
        run = _mk_run()
        run_repo.create(run)
        job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        claimed = job_repo.claim_batch("w1", None, 1, 60)[0]
        epoch = claimed["claim_epoch"]
        assert run_repo.update_status(run.run_id, TENANT_A, dm.RunStatus.COMPLETED,
                                      claim_epoch=epoch + 100) is False
        fetched = run_repo.get(run.run_id, TENANT_A)
        assert fetched.status == dm.RunStatus.RUNNING
        assert run_repo.update_status(run.run_id, TENANT_A, dm.RunStatus.COMPLETED,
                                      claim_epoch=epoch) is True

    def test_terminal_update_sets_ended_at(self, run_repo, job_repo):
        run = _mk_run()
        run_repo.create(run)
        job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        claimed = job_repo.claim_batch("w1", None, 1, 60)[0]
        run_repo.update_status(run.run_id, TENANT_A, dm.RunStatus.COMPLETED,
                              termination_reason=dm.TerminationReason.FINISH,
                              final_answer="done",
                              claim_epoch=claimed["claim_epoch"])
        fetched = run_repo.get(run.run_id, TENANT_A)
        assert fetched.ended_at is not None
        assert fetched.final_answer == "done"

    def test_list_page_pagination_and_status_filter(self, run_repo, job_repo):
        for _ in range(5):
            run = _mk_run()
            run_repo.create(run)
            job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        page1 = run_repo.list_page(TENANT_A, page=1, page_size=2)
        assert page1.total == 5
        assert len(page1.items) == 2
        page3 = run_repo.list_page(TENANT_A, page=3, page_size=2)
        assert len(page3.items) == 1
        claims = {c["run_id"]: c for c in job_repo.claim_batch("w1", None, 10, 60)}
        for r in page1.items:
            c = claims[r.run_id]
            assert run_repo.update_status(r.run_id, TENANT_A, dm.RunStatus.COMPLETED,
                                          claim_epoch=c["claim_epoch"])
        active_page = run_repo.list_page(TENANT_A, status=dm.RunStatus.RUNNING, page=1, page_size=10)
        assert active_page.total == 3
        assert all(r.status == dm.RunStatus.RUNNING for r in active_page.items)

    def test_list_page_tenant_isolated(self, run_repo):
        run_repo.create(_mk_run(tenant=TENANT_A))
        run_repo.create(_mk_run(tenant=TENANT_B))
        for_tenant_a = run_repo.list_page(TENANT_A, page=1, page_size=10)
        assert for_tenant_a.total == 1
        assert all(r.tenant == TENANT_A for r in for_tenant_a.items)

    def test_count_active_runs(self, run_repo, job_repo):
        run_a1 = _mk_run(tenant=TENANT_A)
        run_a2 = _mk_run(tenant=TENANT_A)
        run_a3 = _mk_run(tenant=TENANT_A)
        run_repo.create(run_a1)
        run_repo.create(run_a2)
        run_repo.create(run_a3)
        run_repo.create(_mk_run(tenant=TENANT_B))
        assert run_repo.count_active_runs(TENANT_A) == 3
        job_repo.enqueue(run_id=run_a1.run_id, tenant=TENANT_A, deadline_seconds=3600)
        claimed = job_repo.claim_batch("w1", None, 1, 60)[0]
        assert run_repo.update_status(run_a1.run_id, TENANT_A, dm.RunStatus.COMPLETED,
                                      claim_epoch=claimed["claim_epoch"])
        assert run_repo.count_active_runs(TENANT_A) == 2
        assert run_repo.count_active_runs(TENANT_B) == 1

    def test_count_active_runs_global(self, run_repo):
        run_repo.create(_mk_run(tenant=TENANT_A))
        run_repo.create(_mk_run(tenant=TENANT_B))
        completed = _mk_run(tenant=TENANT_A)
        completed.status = dm.RunStatus.COMPLETED
        run_repo.create(completed)
        assert run_repo.count_active_runs_global() == 2

    def test_count_pending_jobs(self, run_repo, job_repo):
        run_one = _mk_run()
        run_two = _mk_run(tenant=TENANT_B)
        run_repo.create(run_one)
        run_repo.create(run_two)
        assert run_repo.count_active_runs_global() == 2
        assert job_repo.count_pending_jobs() == 0
        job_repo.enqueue(run_id=run_one.run_id, tenant=TENANT_A, deadline_seconds=3600)
        job_repo.enqueue(run_id=run_two.run_id, tenant=TENANT_B, deadline_seconds=3600)
        assert job_repo.count_pending_jobs() == 2
        job_repo.claim_batch("w1", None, 1, 60)
        assert job_repo.count_pending_jobs() == 1


class TestAgentRepository:
    def test_create_and_get(self, run_repo, agent_repo):
        run = _mk_run()
        run_repo.create(run)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        fetched = agent_repo.get(run.run_id, agent.agent_id, TENANT_A)
        assert fetched is not None
        assert fetched.name == "a1"
        assert fetched.status == dm.AgentStatus.QUEUED
        assert fetched.kind == dm.AgentKind.LLM

    def test_list_by_run(self, run_repo, agent_repo):
        run = _mk_run()
        run_repo.create(run)
        agent_repo.create(_mk_agent(run, "a1"))
        agent_repo.create(_mk_agent(run, "a2"))
        agents = agent_repo.list_by_run(run.run_id, TENANT_A)
        assert len(agents) == 2

    def _claimed_run(self, run_repo, job_repo):
        # The agent fence is the run fence (D28): an update must carry the
        # job row's CURRENT claim epoch to land, so every writing test first
        # enqueues + claims its run the way a worker would.
        run = _mk_run()
        run_repo.create(run)
        job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        claimed = job_repo.claim_batch("w1", None, 1, 60)[0]
        return run, claimed["claim_epoch"]

    def test_update_status_running(self, run_repo, agent_repo, job_repo):
        run, epoch = self._claimed_run(run_repo, job_repo)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        ok = agent_repo.update_status(
            run.run_id, agent.agent_id, TENANT_A,
            dm.AgentStatus.RUNNING.value, claim_epoch=epoch,
        )
        assert ok
        fetched = agent_repo.get(run.run_id, agent.agent_id, TENANT_A)
        assert fetched.status == dm.AgentStatus.RUNNING

    def test_update_status_fenced_by_claim_epoch(self, run_repo, agent_repo, job_repo):
        run, epoch = self._claimed_run(run_repo, job_repo)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        assert agent_repo.update_status(
            run.run_id, agent.agent_id, TENANT_A,
            dm.AgentStatus.COMPLETED.value, claim_epoch=epoch + 100,
        ) is False
        assert agent_repo.get(run.run_id, agent.agent_id, TENANT_A).status             == dm.AgentStatus.QUEUED
        assert agent_repo.update_status(
            run.run_id, agent.agent_id, TENANT_A,
            dm.AgentStatus.COMPLETED.value, claim_epoch=epoch,
        ) is True

    def test_update_status_without_a_job_row_is_fenced(self, run_repo, agent_repo):
        # A run someone never enqueued belongs to no slice: a status write
        # with a stale-or-absent epoch has no current-owner to match and is
        # refused, exactly like the run row's own fence.
        run = _mk_run()
        run_repo.create(run)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        assert agent_repo.update_status(
            run.run_id, agent.agent_id, TENANT_A,
            dm.AgentStatus.COMPLETED.value,
        ) is False
        assert agent_repo.get(run.run_id, agent.agent_id, TENANT_A).status \
            == dm.AgentStatus.QUEUED

    def test_update_output_and_error(self, run_repo, agent_repo, job_repo):
        run, epoch = self._claimed_run(run_repo, job_repo)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        agent_repo.update_status(
            run.run_id, agent.agent_id, TENANT_A,
            dm.AgentStatus.FAILED.value,
            error="something went wrong", claim_epoch=epoch,
        )
        fetched = agent_repo.get(run.run_id, agent.agent_id, TENANT_A)
        assert fetched.status == dm.AgentStatus.FAILED
        assert fetched.error == "something went wrong"

    def test_tenant_isolation(self, run_repo, agent_repo):
        run = _mk_run(tenant=TENANT_A)
        run_repo.create(run)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        assert agent_repo.get(run.run_id, agent.agent_id, TENANT_B) is None
        assert agent_repo.list_by_run(run.run_id, TENANT_B) == []


class TestResultShardXOR:

    def test_shard_double_insert_converges_to_the_survivor(
            self, run_repo, agent_repo, shard_repo):
        # The unique pair (uk_shard_agent) is the exactly-once invariant; a
        # racing pod that loses the double-insert must converge to the
        # surviving row rather than surface an IntegrityError.
        run = _mk_run()
        run_repo.create(run)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        first = _mk_shard(run, agent)
        created = shard_repo.create(first)
        again = shard_repo.create(_mk_shard(run, agent))
        shards = shard_repo.list_by_run(run.run_id, TENANT_A)
        assert len(shards) == 1
        assert created.source_agent_id == again.source_agent_id

    def test_shard_with_payload(self, run_repo, agent_repo, shard_repo):
        run = _mk_run()
        run_repo.create(run)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        shard = _mk_shard(run, agent)
        shard_repo.create(shard)
        fetched = shard_repo.get_by_run_agent(run.run_id, agent.agent_id, TENANT_A)
        assert fetched is not None
        assert fetched.payload == {"result": "done"}

    def test_shard_with_artifact_key(self, run_repo, agent_repo, shard_repo):
        run = _mk_run()
        run_repo.create(run)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        shard = dm.ResultShard(
            id=dm.ShardId(uuid.uuid4().hex),
            run_id=run.run_id,
            tenant=TENANT_A,
            source_agent_id=agent.agent_id,
            payload=None,
            artifact_key=dm.ArtifactKey("run/agent/result"),
            routed_at=dm.EpochMillis(_now_ms()),
        )
        shard_repo.create(shard)
        fetched = shard_repo.get_by_run_agent(run.run_id, agent.agent_id, TENANT_A)
        assert fetched is not None
        assert fetched.artifact_key == dm.ArtifactKey("run/agent/result")

    def test_shard_both_rejected(self, run_repo, agent_repo, shard_repo):
        run = _mk_run()
        run_repo.create(run)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        bad = dm.ResultShard(
            id=dm.ShardId(uuid.uuid4().hex),
            run_id=run.run_id,
            tenant=TENANT_A,
            source_agent_id=agent.agent_id,
            payload={"result": "x"},
            artifact_key=dm.ArtifactKey("key"),
            routed_at=dm.EpochMillis(_now_ms()),
        )
        with pytest.raises(ValueError):
            shard_repo.create(bad)

    def test_shard_neither_rejected(self, run_repo, agent_repo, shard_repo):
        run = _mk_run()
        run_repo.create(run)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        bad = dm.ResultShard(
            id=dm.ShardId(uuid.uuid4().hex),
            run_id=run.run_id,
            tenant=TENANT_A,
            source_agent_id=agent.agent_id,
            payload=None,
            artifact_key=None,
            routed_at=dm.EpochMillis(_now_ms()),
        )
        with pytest.raises(ValueError):
            shard_repo.create(bad)

    def test_shard_tenant_isolation(self, run_repo, agent_repo, shard_repo):
        run = _mk_run(tenant=TENANT_A)
        run_repo.create(run)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        shard = _mk_shard(run, agent)
        shard_repo.create(shard)
        assert shard_repo.get_by_run_agent(run.run_id, agent.agent_id, TENANT_B) is None


class TestMessageRepository:
    def test_list_by_agent(self, run_repo, agent_repo, msg_repo):
        run = _mk_run()
        run_repo.create(run)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        for i in range(5):
            msg = dm.Message(
                id=dm.MessageId(uuid.uuid4().hex),
                run_id=run.run_id,
                tenant=TENANT_A,
                agent_id=agent.agent_id,
                seq=i,
                role=dm.MessageRole.ASSISTANT,
                content={"turn": i},
            )
            msg_repo.create(msg)
        msgs = msg_repo.list_by_agent(run.run_id, agent.agent_id, TENANT_A)
        assert len(msgs) == 5
        assert msgs[0].seq == 0
        assert msgs[4].seq == 4

    def test_message_tenant_isolation(self, run_repo, agent_repo, msg_repo):
        run = _mk_run(tenant=TENANT_A)
        run_repo.create(run)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        msg = dm.Message(
            id=dm.MessageId(uuid.uuid4().hex),
            run_id=run.run_id,
            tenant=TENANT_A,
            agent_id=agent.agent_id,
            seq=0,
            role=dm.MessageRole.USER,
            content="hello",
        )
        msg_repo.create(msg)

    def test_apply_retention_keeps_tail(self, run_repo, agent_repo, msg_repo):
        run = _mk_run()
        run_repo.create(run)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        for i in range(6):
            msg_repo.create(dm.Message(
                id=dm.MessageId(uuid.uuid4().hex),
                run_id=run.run_id,
                tenant=TENANT_A,
                agent_id=agent.agent_id,
                seq=i,
                role=dm.MessageRole.ASSISTANT,
                content={"turn": i},
            ))
        deleted = msg_repo.apply_retention(run.run_id, agent.agent_id, TENANT_A, tail=2)
        assert deleted == 4
        kept = msg_repo.list_by_agent(run.run_id, agent.agent_id, TENANT_A)
        assert [m.seq for m in kept] == [4, 5]

    def test_apply_retention_noop_when_under_tail(self, run_repo, agent_repo, msg_repo):
        run = _mk_run()
        run_repo.create(run)
        agent = _mk_agent(run)
        agent_repo.create(agent)
        msg_repo.create(dm.Message(
            id=dm.MessageId(uuid.uuid4().hex),
            run_id=run.run_id,
            tenant=TENANT_A,
            agent_id=agent.agent_id,
            seq=0,
            role=dm.MessageRole.USER,
            content="only",
        ))
        assert msg_repo.apply_retention(run.run_id, agent.agent_id, TENANT_A, tail=50) == 0
        assert len(msg_repo.list_by_agent(run.run_id, agent.agent_id, TENANT_A)) == 1
        assert msg_repo.list_by_agent(run.run_id, agent.agent_id, TENANT_B) == []


class TestRoundRepository:
    def test_create_and_list(self, run_repo, round_repo):
        run = _mk_run()
        run_repo.create(run)
        for i in range(3):
            r = dm.Round(
                id=dm.RoundId(uuid.uuid4().hex),
                run_id=run.run_id,
                tenant=TENANT_A,
                stage_index=i,
                orchestrator_steps=1,
                max_subagent_steps=2,
                cohort_agent_ids=(dm.AgentId("a"),),
                started_at=dm.EpochMillis(_now_ms()),
            )
            round_repo.create(r)
        rounds = round_repo.list_by_run(run.run_id, TENANT_A)
        assert len(rounds) == 3


class TestGuardEventRepository:
    def test_create_and_list(self, run_repo, guard_repo):
        run = _mk_run()
        run_repo.create(run)
        ge = dm.GuardEvent(
            id=dm.EventId(uuid.uuid4().hex),
            run_id=run.run_id,
            tenant=TENANT_A,
            agent_id=None,
            kind=dm.GuardKind.SERIAL_COLLAPSE,
            details={"turns": 10},
            created_at=dm.EpochMillis(_now_ms()),
        )
        guard_repo.create(ge)
        events = guard_repo.list_by_run(run.run_id, TENANT_A)
        assert len(events) == 1
        assert events[0].kind == dm.GuardKind.SERIAL_COLLAPSE


class TestJobRepository:
    def test_enqueue_and_claim(self, run_repo, job_repo):
        run = _mk_run()
        run_repo.create(run)
        job_repo.enqueue(
            run_id=run.run_id,
            tenant=TENANT_A,
            deadline_seconds=3600,
        )
        claimed = job_repo.claim_batch("worker-1", None, 1, 60)
        assert len(claimed) == 1
        assert claimed[0]["run_id"] == run.run_id
        assert claimed[0]["claim_epoch"] == 1
        assert claimed[0]["attempts"] == 1

    def test_claim_increments_epoch(self, run_repo, job_repo):
        run = _mk_run()
        run_repo.create(run)
        job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        c1 = job_repo.claim_batch("w1", None, 1, 60)
        assert c1[0]["claim_epoch"] == 1
        job_repo.reschedule(run.run_id, TENANT_A, "w1", 0.1, None, 1)
        import time as t
        t.sleep(0.2)
        c2 = job_repo.claim_batch("w2", None, 1, 60)
        assert c2[0]["claim_epoch"] == 2

    def test_renew_lease_by_valid_holder(self, run_repo, job_repo):
        run = _mk_run()
        run_repo.create(run)
        job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        claimed = job_repo.claim_batch("w1", None, 1, 60)
        epoch = claimed[0]["claim_epoch"]
        ok = job_repo.renew_lease(run.run_id, TENANT_A, "w1", 60, epoch)
        assert ok

    def test_renew_lease_rejected_by_stale_epoch(self, run_repo, job_repo):
        run = _mk_run()
        run_repo.create(run)
        job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        claimed = job_repo.claim_batch("w1", None, 1, 60)
        real_epoch = claimed[0]["claim_epoch"]
        stale_epoch = real_epoch + 100
        assert not job_repo.renew_lease(run.run_id, TENANT_A, "w1", 60, stale_epoch)

    def test_complete(self, run_repo, job_repo):
        run = _mk_run()
        run_repo.create(run)
        job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        claimed = job_repo.claim_batch("w1", None, 1, 60)
        ok = job_repo.complete(run.run_id, TENANT_A, "w1", claimed[0]["claim_epoch"])
        assert ok

    def test_fail(self, run_repo, job_repo):
        run = _mk_run()
        run_repo.create(run)
        job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        claimed = job_repo.claim_batch("w1", None, 1, 60)
        ok = job_repo.fail(run.run_id, TENANT_A, "error", "w1", claimed[0]["claim_epoch"])
        assert ok

    def test_cancel_flow(self, run_repo, job_repo):
        run = _mk_run()
        run_repo.create(run)
        job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        assert not job_repo.is_cancel_requested(run.run_id, TENANT_A)
        job_repo.request_cancel(run.run_id, TENANT_A)
        assert job_repo.is_cancel_requested(run.run_id, TENANT_A)

    def test_claim_visible_in_batch(self, run_repo, job_repo):
        run_repo.create(_mk_run())
        run_repo.create(_mk_run())
        job_repo.enqueue(run_id=_mk_run().run_id, tenant=TENANT_A, deadline_seconds=3600)
        job_repo.enqueue(run_id=_mk_run().run_id, tenant=TENANT_A, deadline_seconds=3600)
        claimed = job_repo.claim_batch("w1", None, 10, 60)
        assert len(claimed) >= 1

    def test_reclaim_batch_picks_up_expired_leases(self, run_repo, job_repo):
        run = _mk_run()
        run_repo.create(run)
        job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        claimed = job_repo.claim_batch("w1", None, 1, 60)
        assert claimed[0]["claim_epoch"] == 1
        assert job_repo.reclaim_batch("w2", 10, 60) == []
        job_repo.renew_lease(run.run_id, TENANT_A, "w1", -1, claimed[0]["claim_epoch"])
        reclaimed = job_repo.reclaim_batch("w2", 10, 60)
        assert len(reclaimed) == 1
        assert reclaimed[0]["claim_epoch"] == 2
        assert reclaimed[0]["reclaims"] == 1
        again = job_repo.claim_batch("w3", None, 10, 60)
        assert again == []

    def test_reclaim_increments_reclaims_per_recovery(self, run_repo, job_repo):
        run = _mk_run()
        run_repo.create(run)
        job_repo.enqueue(run_id=run.run_id, tenant=TENANT_A, deadline_seconds=3600)
        claimed = job_repo.claim_batch("w1", None, 1, 60)
        epoch = claimed[0]["claim_epoch"]
        job_repo.renew_lease(run.run_id, TENANT_A, "w1", -1, epoch)
        r1 = job_repo.reclaim_batch("w2", 10, 60)
        assert r1[0]["reclaims"] == 1
        job_repo.renew_lease(run.run_id, TENANT_A, "w2", -1, r1[0]["claim_epoch"])
        r2 = job_repo.reclaim_batch("w3", 10, 60)
        assert r2[0]["reclaims"] == 2
        assert r2[0]["claim_epoch"] == r1[0]["claim_epoch"] + 1


class TestMandatoryColumns:
    def test_all_tables_have_mandatory_columns(self):
        import sqlalchemy as sa
        engine = sa.create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[
            om.AcAgentComputeRunModel.__table__,
            om.AcAgentComputeAgentModel.__table__,
            om.AcAgentComputeMessageModel.__table__,
            om.AcAgentComputeResultShardModel.__table__,
            om.AcAgentComputeRoundModel.__table__,
            om.AcAgentComputeGuardEventModel.__table__,
            om.AcAgentComputeJobModel.__table__,
            om.AcAgentComputeAuditModel.__table__,
        ])
        insp = sa.inspect(engine)
        for table_name in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns(table_name)}
            for required in ("id", "gmt_create", "gmt_modified", "env", "tenant", "is_deleted"):
                assert required in cols, f"{table_name} missing {required}"

    def test_unique_indexes_scoped_by_tenant(self):
        for model in om.ALL_MODELS:
            for idx in model.__table__.indexes:
                if idx.unique:
                    names = {c.name for c in idx.columns}
                    assert "env" in names, f"{model.__tablename__}.{idx.name} not env-scoped"
                    assert "tenant" in names, f"{model.__tablename__}.{idx.name} not tenant-scoped"

    def test_orm_tables_not_pydantic(self):
        import pydantic
        for model in om.ALL_MODELS:
            assert not issubclass(model, pydantic.BaseModel), \
                f"{model.__name__} inherits pydantic.BaseModel"

    def test_repository_impl_no_await_in_session(self):
        # D19: every per-table impl.py must keep its session block sync.
        import ast, pathlib
        impl_dir = pathlib.Path(
            "src/agentclaw/community/core/agentcompute/repository"
        )
        impl_files = sorted(impl_dir.glob("*/impl.py"))
        assert impl_files, "no per-table impl.py found under repository/"
        for impl_path in impl_files:
            tree = ast.parse(impl_path.read_text())
            for node in ast.walk(tree):
                assert not isinstance(node, (ast.Await, ast.AsyncFor)), \
                    f"async construct at {impl_path.name}:{getattr(node, 'lineno', '?')}"

    def test_ddl_orm_column_parity(self):
        import pathlib, re
        # Agentcompute DDL ships as dated forward files (migration policy 12.3);
        # parity must hold across every file in sql/, not a single name.
        ddl_dir = pathlib.Path("src/agentclaw/community/core/agentcompute/sql")
        ddl_text = "\n".join(p.read_text() for p in sorted(ddl_dir.glob("*.sql")))
        ddl_tables: dict[str, set[str]] = {}
        for match in re.finditer(
            r"CREATE TABLE IF NOT EXISTS `(\w+)`\s*\((.*?)\n\) ", ddl_text, re.DOTALL
        ):
            table_name, body = match.group(1), match.group(2)
            cols = set(re.findall(r"^\s+`(\w+)`", body, re.MULTILINE))
            ddl_tables[table_name] = cols
        assert len(ddl_tables) == len(om.ALL_MODELS)
        assert len(ddl_tables) >= 8  # 7 swarm tables + append-only audit
        for model in om.ALL_MODELS:
            orm_cols = {c.name for c in model.__table__.columns}
            ddl_cols = ddl_tables[model.__tablename__]
            missing = orm_cols - (ddl_cols | {"id", "gmt_create", "gmt_modified"})
            assert not missing, (
                f"{model.__tablename__}: ORM columns absent from OceanBase DDL: {missing}"
            )
        for table_name, cols in ddl_tables.items():
            for required in ("env", "tenant", "is_deleted"):
                assert required in cols, f"{table_name}: {required} missing in DDL"