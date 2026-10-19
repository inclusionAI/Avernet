"""MessageRepository guards: seq BigInteger (P0 int32 overflow), uk_message
IntegrityError convergence, and _to_domain field restore.

next_seq() (runtime/store/impl.py) mints epoch milliseconds (~1.79e12),
beyond int32 max 2147483647 — MySQL/OceanBase strict mode rejects every
message INSERT while `seq` is int, which is why the ORM column type is
pinned here. SQLite accepts 64-bit values under INTEGER affinity, so the
DB-level insert alone proves little; the pinned introspection does.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import BigInteger, create_engine, update
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.message import model as om

TENANT = dm.TenantId("tenant-a")


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
def msg_repo():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[om.AcAgentSwarmMessageModel.__table__])
    yield MessageRepository(_TestDB(engine))
    engine.dispose()


def _mk_message(agent_id: dm.AgentId, seq: int, **overrides) -> dm.Message:
    fields = dict(
        id=dm.MessageId(uuid.uuid4().hex),
        run_id=dm.RunId(uuid.uuid4().hex),
        tenant=TENANT,
        agent_id=agent_id,
        seq=seq,
        role=dm.MessageRole.ASSISTANT,
        content={"turn": seq},
    )
    fields.update(overrides)
    return dm.Message(**fields)


class TestSeqColumnBigInteger:
    def test_model_declares_biginteger(self):
        seq_type = om.AcAgentSwarmMessageModel.__table__.columns.seq.type
        assert isinstance(seq_type, BigInteger), (
            f"seq must be BigInteger (epoch-ms writer overflows int32), "
            f"found {seq_type!r}"
        )

    def test_epoch_ms_seq_insert_round_trips(self, msg_repo):
        msg = _mk_message(dm.AgentId(uuid.uuid4().hex), 1_790_000_000_123)
        assert msg_repo.create(msg).seq == 1_790_000_000_123
        (row,) = msg_repo.list_by_agent(msg.run_id, msg.agent_id, TENANT)
        assert row.seq == 1_790_000_000_123


class TestCreateConvergesOnUniqueConflict:
    def test_duplicate_agent_seq_converges_to_existing(self, msg_repo):
        agent = dm.AgentId(uuid.uuid4().hex)
        first = _mk_message(
            agent, seq=42, content={"winner": True},
            decision=dm.WaitDecision(refs=("scout",), mode=dm.WaitMode.ANY),
            token_usage=dm.TokenUsage(prompt_tokens=11, completion_tokens=7,
                                      total_tokens=18),
            artifact_key=dm.ArtifactKey("run/agent/result"),
        )
        msg_repo.create(first)
        racer = _mk_message(agent, seq=42, content={"winner": False})
        converged = msg_repo.create(racer)
        assert converged.id == first.id
        assert converged.content == {"winner": True}
        assert converged.decision == first.decision
        assert converged.token_usage == first.token_usage
        assert converged.artifact_key == first.artifact_key
        assert len(msg_repo.list_by_agent(first.run_id, agent, TENANT)) == 1


class TestToDomainRestoresFields:
    def test_round_trips_decision_token_usage_artifact_key(self, msg_repo):
        agent = dm.AgentId(uuid.uuid4().hex)
        run = dm.RunId(uuid.uuid4().hex)
        originals = (
            _mk_message(agent, seq=1, run_id=run,
                        role=dm.MessageRole.DECISION,
                        decision=dm.WaitDecision(
                            refs=("scout", "worker"), mode=dm.WaitMode.ANY),
                        token_usage=dm.TokenUsage(3, 5, 8),
                        artifact_key=dm.ArtifactKey("run/agent/result")),
            _mk_message(agent, seq=2, run_id=run,
                        role=dm.MessageRole.DECISION,
                        decision=dm.HandoffDecision(target="legacy",
                                                   reason="fallback")),
            _mk_message(agent, seq=3, run_id=run,
                        role=dm.MessageRole.DECISION,
                        decision=dm.FinishDecision(answer="done")),
        )
        for original in originals:
            msg_repo.create(original)
        fetched = msg_repo.list_by_agent(run, agent, TENANT)
        assert [m.seq for m in fetched] == [1, 2, 3]
        for original, back in zip(originals, fetched):
            assert back.decision == original.decision
            assert back.token_usage == original.token_usage
            assert back.artifact_key == original.artifact_key

    def test_absent_fields_decode_to_none(self, msg_repo):
        msg = _mk_message(dm.AgentId(uuid.uuid4().hex), seq=0,
                          decision=None, token_usage=None, artifact_key=None)
        msg_repo.create(msg)
        (fetched,) = msg_repo.list_by_agent(msg.run_id, msg.agent_id, TENANT)
        assert fetched.decision is None
        assert fetched.token_usage is None
        assert fetched.artifact_key is None
        assert fetched.created_at == dm.EpochMillis(0)


def _mk_envelope(name: str) -> dm.TaskEnvelope:
    return dm.TaskEnvelope(
        name=name,
        objective=f"go find {name} evidence",
        output_schema={"type": "object",
                       "properties": {"summary": {"type": "string"}}},
        boundaries="only public sources",
        parent_goal="map the incident to owning services",
    )


class TestSpawnDecisionRoundTrip:
    """Encode/decode symmetry for the one decision kind carrying
    dataclass payloads (round-2 finding): `create()` serializing
    `decision.__dict__` through `_dumps(default=str)` collapsed every
    TaskEnvelope in SpawnDecision.agents to its repr string, so spawn
    silently mis-decoded while wait/handoff/finish round-tripped fine.
    """

    def test_spawn_envelopes_round_trip_losslessly(self, msg_repo):
        agent = dm.AgentId(uuid.uuid4().hex)
        run = dm.RunId(uuid.uuid4().hex)
        original = _mk_message(
            agent, seq=1, run_id=run, role=dm.MessageRole.DECISION,
            decision=dm.SpawnDecision(agents=(
                _mk_envelope("scout"), _mk_envelope("worker"),
            )),
        )
        msg_repo.create(original)
        (back,) = msg_repo.list_by_agent(run, agent, TENANT)
        assert back.decision == original.decision
        assert all(isinstance(e, dm.TaskEnvelope)
                   for e in back.decision.agents)
        assert [e.name for e in back.decision.agents] == ["scout", "worker"]
        assert back.decision.agents[0].output_schema == \
            original.decision.agents[0].output_schema

    def test_legacy_repr_string_agents_never_decode_silently(self, msg_repo):
        # Rows written before the symmetry fix persist each envelope as
        # its repr string. Decoding must refuse loudly (ValueError), never
        # build a SpawnDecision whose typed tuple[TaskEnvelope, ...]
        # silently holds those strings.
        agent = dm.AgentId(uuid.uuid4().hex)
        run = dm.RunId(uuid.uuid4().hex)
        msg = _mk_message(agent, seq=1, run_id=run)
        msg_repo.create(msg)
        legacy_env = dm.TaskEnvelope(
            name="scout", objective="x", output_schema={"type": "object"},
            boundaries="", parent_goal="",
        )
        with msg_repo._db.orm_session() as session:
            session.execute(
                update(om.AcAgentSwarmMessageModel)
                .where(om.AcAgentSwarmMessageModel.message_id == msg.id)
                .values(decision=json.dumps({
                    "kind": dm.DecisionKind.SPAWN_AGENT.value,
                    "agents": [repr(legacy_env)],
                })),
            )
        with pytest.raises(ValueError, match="non-dict agent"):
            msg_repo.list_by_agent(run, agent, TENANT)


class TestUnknownKindDecisionDecodesToNone:

    def test_unknown_kind_decision_decodes_to_none_not_raise(self, msg_repo):
        # D18 closed-set tolerance: a decision row whose `kind` is outside
        # DecisionKind decodes to None instead of raising, so the message
        # row and its non-decision payload stay readable.
        agent = dm.AgentId(uuid.uuid4().hex)
        run = dm.RunId(uuid.uuid4().hex)
        msg = _mk_message(agent, seq=1, run_id=run, role=dm.MessageRole.DECISION)
        msg_repo.create(msg)
        with msg_repo._db.orm_session() as session:
            session.execute(
                update(om.AcAgentSwarmMessageModel)
                .where(om.AcAgentSwarmMessageModel.message_id == msg.id)
                .values(decision=json.dumps({"kind": "hyperspace", "x": 1})),
            )
        (fetched,) = msg_repo.list_by_agent(run, agent, TENANT)
        assert fetched.decision is None
        assert fetched.content == {"turn": 1}


class TestNonConvergingIntegrityErrorReRaises:

    def test_replay_against_soft_deleted_survivor_re_raises(self, msg_repo):
        # Only a LIVE (env, tenant, agent_id, seq) survivor converges. The
        # raw unique index still holds the slot for a soft-deleted row,
        # but _existing_by_agent_seq filters is_deleted — with no live row
        # to converge onto, the replay's IntegrityError must re-raise
        # rather than fabricate a winner (D24 exactly-once).
        from sqlalchemy.exc import IntegrityError

        agent = dm.AgentId(uuid.uuid4().hex)
        first = _mk_message(agent, seq=7)
        msg_repo.create(first)
        with msg_repo._db.orm_session() as session:
            session.execute(
                update(om.AcAgentSwarmMessageModel)
                .where(om.AcAgentSwarmMessageModel.message_id == first.id)
                .values(is_deleted=True),
            )
        racer = _mk_message(agent, seq=7, content={"racer": True})
        with pytest.raises(IntegrityError):
            msg_repo.create(racer)
        assert msg_repo.list_by_agent(first.run_id, agent, TENANT) == []