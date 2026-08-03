"""TDD for the task ORM models (Phase 1.1, plan §1.1).

Builds the 3 tables on a real in-memory SQLite (create_all), inserts + queries
rows, and asserts the AutoIncrementBigInteger autoincrement works on SQLite.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Side-effect import: registers the 3 task ORM classes on Base.metadata.
from agentclaw.community.core.task.repository.models import (  # noqa: F401
    AcTaskEventModel,
    AcTaskExecutionGraphModel,
    AcTaskModel,
)
from agentclaw.community.core.base import Base

pytestmark = pytest.mark.unit


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    factory = sessionmaker(bind=engine, autoflush=False)
    db = factory()
    yield db
    db.close()


def test_three_task_tables_created(engine):
    names = set(Base.metadata.tables.keys())
    assert "ac_task" in names
    assert "ac_task_event" in names
    assert "ac_task_execution_graph" in names


def test_ac_task_insert_query_autoincrement(session):
    row = AcTaskModel(task_id="task-1", user_id="u1", status="drafting")
    session.add(row)
    session.flush()
    assert row.id is not None and row.id > 0
    fetched = session.query(AcTaskModel).filter(AcTaskModel.task_id == "task-1").one()
    assert fetched.status == "drafting"
    assert fetched.loop_round == 0


def test_ac_task_autoincrement_monotonic(session):
    a = AcTaskModel(task_id="task-a", user_id="u1")
    b = AcTaskModel(task_id="task-b", user_id="u1")
    session.add_all([a, b])
    session.flush()
    assert b.id > a.id


def test_ac_task_event_append_only_has_no_gmt_modified():
    cols = {c.name for c in AcTaskEventModel.__table__.columns}
    assert "gmt_create" in cols
    assert "gmt_modified" not in cols  # events are immutable
    assert "seq" in cols
    assert "kind" in cols


def test_ac_task_event_unique_seq_per_task(session):
    session.add(
        AcTaskEventModel(task_id="task-1", seq=1, kind="task.created")
    )
    session.flush()
    # (env, task_id, seq) unique → second seq=1 must raise on commit/flush
    from sqlalchemy.exc import IntegrityError

    session.add(AcTaskEventModel(task_id="task-1", seq=1, kind="node.running"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_ac_task_event_seq_ordered_load(session):
    for seq in (1, 2, 3):
        session.add(AcTaskEventModel(task_id="t", seq=seq, kind="task.created"))
    session.flush()
    rows = session.query(AcTaskEventModel).order_by(AcTaskEventModel.seq.asc()).all()
    assert [r.seq for r in rows] == [1, 2, 3]


def test_ac_task_execution_graph_has_graph_text_and_version(session):
    row = AcTaskExecutionGraphModel(task_id="task-1", graph="{}", version=3)
    session.add(row)
    session.flush()
    fetched = session.query(AcTaskExecutionGraphModel).filter(
        AcTaskExecutionGraphModel.task_id == "task-1"
    ).one()
    assert fetched.graph == "{}"
    assert fetched.version == 3


def test_ac_task_indexes_present():
    tindexes = {ix.name for ix in AcTaskModel.__table__.indexes}
    eindexes = {ix.name for ix in AcTaskEventModel.__table__.indexes}
    assert "idx_ac_task_env_status" in tindexes
    assert "idx_ac_task_env_user" in tindexes
    assert "idx_ac_task_env_uuid" in tindexes
    assert "idx_ac_task_event_env_task_seq" in eindexes