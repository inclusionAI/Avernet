"""Integration tests for PublishOperationRepository against in-memory SQLite.

The same single ORM body runs on prod OceanBase, so the CAS state transitions,
the ``uk_op`` unique key, and the JSON round-trips are all exercised against a
real database here.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Side-effect import: registers PublishOperationModel on Base.metadata so
# create_all() builds the ac_publish_operation table.
from agentclaw.community.core.service_bot.repository.models import (  # noqa: F401
    PublishOperationKind,
    PublishOperationModel,
    PublishOperationState,
)
from agentclaw.community.plugins.publish_operation_repository import (
    PublishOperationRepository,
)

ENV = "dev"


class InMemorySqliteDB:
    def __init__(self, engine):
        self._session_factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def repo():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return PublishOperationRepository(InMemorySqliteDB(engine))


def _intent(repo, *, publish_id=1, kind=PublishOperationKind.ONLINE_UPGRADE.value,
            stage="online", attempt=1, request_id="req-1", bot_uuid="", params=None,
            operator="op", env=ENV):
    return repo.insert(
        {
            "publish_id": publish_id,
            "operation_kind": kind,
            "stage": stage,
            "attempt": attempt,
            "request_id": request_id,
            "bot_uuid": bot_uuid,
            "params": params,
            "operator": operator,
            "env": env,
        }
    )


# ── insert / identity ─────────────────────────────────────────────────────────
def test_insert_defaults_pending(repo):
    op = _intent(repo)
    assert op.id is not None
    assert op.state == PublishOperationState.PENDING.value
    assert op.attempt == 1
    assert op.baas_publish_id is None


def test_insert_roundtrips_params_json(repo):
    op = _intent(repo, params={"version": 3, "nested": {"a": [1, 2]}})
    fetched = repo.get_by_id(op.id)
    assert fetched.params == {"version": 3, "nested": {"a": [1, 2]}}


def test_uk_op_conflict_rejected(repo):
    _intent(repo, publish_id=7, attempt=1)
    with pytest.raises(IntegrityError):
        _intent(repo, publish_id=7, attempt=1)


def test_get_by_key_and_latest_by_kind(repo):
    _intent(repo, publish_id=5, attempt=1)
    _intent(repo, publish_id=5, attempt=2)
    exact = repo.get_by_key(5, PublishOperationKind.ONLINE_UPGRADE.value, "online", 1)
    assert exact.attempt == 1
    latest = repo.get_latest_by_kind(5, PublishOperationKind.ONLINE_UPGRADE.value, "online")
    assert latest.attempt == 2


def test_max_attempt(repo):
    assert repo.max_attempt(9, PublishOperationKind.RESTART.value, "online") == 0
    _intent(repo, publish_id=9, kind=PublishOperationKind.RESTART.value, attempt=1)
    _intent(repo, publish_id=9, kind=PublishOperationKind.RESTART.value, attempt=3)
    assert repo.max_attempt(9, PublishOperationKind.RESTART.value, "online") == 3


def test_list_by_publish_and_bot(repo):
    _intent(repo, publish_id=11, attempt=1, bot_uuid="bot-a")
    _intent(repo, publish_id=11, attempt=2, bot_uuid="bot-b")
    _intent(repo, publish_id=12, attempt=1, bot_uuid="bot-a")
    by_pub = repo.list_by_publish(11)
    assert len(by_pub) == 2
    by_bot = repo.list_by_bot("bot-a", ENV)
    assert {o.publish_id for o in by_bot} == {11, 12}
    assert repo.list_by_bot("bot-a", "other-env") == []


# ── CAS transitions ───────────────────────────────────────────────────────────
def test_record_workflow_cas(repo):
    op = _intent(repo)
    updated = repo.record_workflow(op.id, baas_publish_id=555, bot_uuid="bot-x")
    assert updated.state == PublishOperationState.ID_RECORDED.value
    assert updated.baas_publish_id == 555
    assert updated.bot_uuid == "bot-x"
    # Second record_workflow loses the CAS (no longer PENDING).
    assert repo.record_workflow(op.id, baas_publish_id=999) is None
    assert repo.get_by_id(op.id).baas_publish_id == 555


def test_complete_cas(repo):
    op = _intent(repo)
    # complete requires ID_RECORDED first.
    assert repo.complete(op.id) is None
    repo.record_workflow(op.id, baas_publish_id=1)
    done = repo.complete(op.id)
    assert done.state == PublishOperationState.COMPLETED.value
    # idempotent re-complete loses the CAS.
    assert repo.complete(op.id) is None


def test_fail_from_any_nonterminal(repo):
    op = _intent(repo)
    failed = repo.fail(op.id, "boom")
    assert failed.state == PublishOperationState.FAILED.value
    assert failed.last_error == "boom"
    # cannot fail a terminal row.
    assert repo.fail(op.id, "again") is None


def test_abandon_from_id_recorded(repo):
    op = _intent(repo)
    repo.record_workflow(op.id, baas_publish_id=1)
    ab = repo.abandon(op.id, "superseded")
    assert ab.state == PublishOperationState.ABANDONED.value
    assert ab.last_error == "superseded"
    assert repo.abandon(op.id, "again") is None


def test_update_result_blind_overwrite(repo):
    op = _intent(repo)
    r1 = repo.update_result(op.id, {"binding_id": 10})
    assert r1.result == {"binding_id": 10}
    r2 = repo.update_result(op.id, {"binding_id": 10, "draft_id": 20})
    assert r2.result == {"binding_id": 10, "draft_id": 20}
    assert repo.update_result(999, {"x": 1}) is None


def test_transitions_on_missing_row_return_none(repo):
    assert repo.record_workflow(123456, baas_publish_id=1) is None
    assert repo.complete(123456) is None
    assert repo.fail(123456, "e") is None
    assert repo.abandon(123456, "r") is None
