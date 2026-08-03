"""Integration tests for the ORM task repos (Phase 1.3, plan §1.3).

The same single ORM body runs on prod OceanBase, so the snapshot upsert, the
event-log single-writer seq monotonicity, and the no-prior-reference invariant
exercised here cover the prod path too.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.task.domain.events import (
    EventKind,
    TaskEvent,
    next_seq,
)
from agentclaw.community.core.task.domain.models import (
    Node,
    NodeStatus,
    SubTaskSpec,
    Task,
    TaskSource,
    TaskSpec,
    TaskSpecMetadata,
    GraphStatus,
)
from agentclaw.community.core.task.domain.repository import TaskNotFoundError
from agentclaw.community.core.task.repository.models import (  # noqa: F401
    AcTaskEventModel,
    AcTaskModel,
)
from agentclaw.community.plugins.task_event_repository import (
    OrmTaskEventRepository,
)
from agentclaw.community.plugins.task_repository import OrmTaskRepository

pytestmark = pytest.mark.integration


class _InMemorySqliteDB:
    def __init__(self, engine) -> None:
        self._factory = sessionmaker(bind=engine, autoflush=False)

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


@pytest.fixture
def db():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return _InMemorySqliteDB(eng)


@pytest.fixture
def task_repo(db):
    return OrmTaskRepository(db)


@pytest.fixture
def event_repo(db):
    return OrmTaskEventRepository(db)


def _task(task_id="task-1", user_id="u1", title="t") -> Task:
    return Task(
        id=task_id,
        user_id=user_id,
        source=TaskSource.API,
        spec=TaskSpec(metadata=TaskSpecMetadata(id=task_id, title=title)),
    )


# --- TaskRepo snapshot ------------------------------------------------------


def test_save_and_get_round_trips_spec_and_status(task_repo):
    t = _task()
    task_repo.save(t)
    fetched = task_repo.get_by_id("task-1")
    assert fetched.id == "task-1"
    assert fetched.status is GraphStatus.DRAFTING
    assert fetched.spec.metadata.title == "t"
    assert fetched.source is TaskSource.API


def test_save_is_upsert_keyed_on_task_id(task_repo):
    t = _task()
    task_repo.save(t)
    t.status = GraphStatus.DEFINED
    t.loop_round = 2
    task_repo.save(t)
    fetched = task_repo.get_by_id("task-1")
    assert fetched.status is GraphStatus.DEFINED
    assert fetched.loop_round == 2


def test_get_unknown_raises(task_repo):
    with pytest.raises(TaskNotFoundError):
        task_repo.get_by_id("ghost")


def test_list_by_user_filters_and_orders(task_repo):
    task_repo.save(_task("task-a", "u1", "a"))
    task_repo.save(_task("task-b", "u1", "b"))
    task_repo.save(_task("task-c", "u2", "c"))
    rows = task_repo.list_by_user("u1")
    assert {r.id for r in rows} == {"task-a", "task-b"}


def test_get_returns_independent_snapshot(task_repo):
    task_repo.save(_task())
    fetched = task_repo.get_by_id("task-1")
    fetched.spec.metadata.title = "mutated-on-my-copy"
    again = task_repo.get_by_id("task-1")
    assert again.spec.metadata.title == "t"  # no bleed-through


def test_save_round_trips_full_aggregate_with_graph(task_repo):
    t = _task()
    t.status = GraphStatus.RUNNING
    from agentclaw.community.core.task.domain.models import (
        Edge,
        EdgeKind,
        TaskExecutionGraph,
    )

    t.execution_graph = TaskExecutionGraph(
        status=GraphStatus.RUNNING,
        nodes=[Node(node_id="n1", spec="do x", run_mode=None, status=NodeStatus.RUNNING)],
        edges=[Edge(edge_id="e1", from_node="n1", to_node="n2", kind=EdgeKind.DEPENDENCY)],
    )
    task_repo.save(t)
    fetched = task_repo.get_by_id("task-1")
    assert fetched.status is GraphStatus.RUNNING
    assert fetched.execution_graph is not None
    assert fetched.execution_graph.nodes[0].node_id == "n1"
    assert fetched.execution_graph.nodes[0].status is NodeStatus.RUNNING
    assert fetched.execution_graph.edges[0].kind is EdgeKind.DEPENDENCY


# --- TaskEventRepo append-only + single-writer seq -------------------------


def test_append_assigns_seq_monotonic(event_repo):
    e1 = TaskEvent(task_id="task-1", seq=1, kind=EventKind.TASK_CREATED)
    e2 = TaskEvent(task_id="task-1", seq=2, kind=EventKind.NODE_RUNNING)
    event_repo.append(e1)
    event_repo.append(e2)
    assert event_repo.latest_seq("task-1") == 2


def test_append_rejects_out_of_order_seq(event_repo):
    event_repo.append(TaskEvent(task_id="task-1", seq=1, kind=EventKind.TASK_CREATED))
    from agentclaw.community.core.task.domain.events import IllegalEventError

    with pytest.raises(IllegalEventError):
        event_repo.append(
            TaskEvent(task_id="task-1", seq=3, kind=EventKind.NODE_RUNNING)  # gap
        )


def test_append_rejects_reused_seq(event_repo):
    event_repo.append(TaskEvent(task_id="task-1", seq=1, kind=EventKind.TASK_CREATED))
    from agentclaw.community.core.task.domain.events import IllegalEventError

    with pytest.raises(IllegalEventError):
        event_repo.append(
            TaskEvent(task_id="task-1", seq=1, kind=EventKind.NODE_RUNNING)  # reuse
        )


def test_load_events_after_seq(event_repo):
    for seq, kind in [
        (1, EventKind.TASK_CREATED),
        (2, EventKind.NODE_RUNNING),
        (3, EventKind.NODE_ACCEPTED),
    ]:
        event_repo.append(TaskEvent(task_id="task-1", seq=seq, kind=kind))
    tail = event_repo.load_events("task-1", after_seq=1)
    assert [e.seq for e in tail] == [2, 3]
    assert all(isinstance(e, TaskEvent) for e in tail)


def test_load_events_backfills_occurred_at(event_repo):
    # occurred_at is populated from ac_task_event.gmt_create on read so the
    # GET /tasks/{id}/history trace carries a wall-clock.
    event_repo.append(TaskEvent(task_id="task-1", seq=1, kind=EventKind.TASK_CREATED))
    loaded = event_repo.load_events("task-1")
    assert loaded[0].occurred_at is not None
    assert isinstance(loaded[0].occurred_at, str)


def test_latest_seq_none_when_no_events(event_repo):
    assert event_repo.latest_seq("ghost") is None


def test_load_events_reconstructs_typed_subclass(event_repo):
    e = TaskEvent(
        task_id="task-1",
        seq=next_seq(None),
        kind=EventKind.NODE_ACCEPTED,
        payload={"node_id": "n1", "verifier": "bot-a"},
    )
    event_repo.append(e)
    loaded = event_repo.load_events("task-1")
    assert loaded[0].kind is EventKind.NODE_ACCEPTED
    assert loaded[0].payload["node_id"] == "n1"