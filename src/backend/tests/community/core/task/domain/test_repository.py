"""TDD for TaskRepo / TaskEventRepo protocols (Phase 0.4).

These Protocols are the persistence seam: TaskService depends on them, the
plugin layer implements them (community=SQLite ORM via DatabasePlugin,
corp=同一 body 经 ZDAS). Phase 0.4 only fixes the *contract*; impl lands in
Phase 1. Tests use an in-memory Fake to assert the contract:
- TaskRepo.save/get round-trips the aggregate (incl. execution_graph).
- TaskEventRepo.append is the single writer that assigns monotonic seq.
- load_events returns the log sorted by seq.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.events import (
    TaskCreated,
    TaskEvent,
    next_seq,
)
from agentclaw.community.core.task.domain.models import (
    Node,
    NodeStatus,
    TaskExecutionGraph,
    TaskSource,
    TaskSpec,
    TaskSpecMetadata,
    GraphStatus,
)
from agentclaw.community.core.task.domain.repository import (
    EventNotFoundError,
    TaskEventRepo,
    TaskNotFoundError,
    TaskRepo,
)


# --- in-memory contract harness --------------------------------------------

class _InMemoryTaskRepo:
    def __init__(self) -> None:
        self._store: dict[str, object] = {}

    def save(self, task: object) -> None:
        import copy
        self._store[task.id] = copy.deepcopy(task)

    def get_by_id(self, task_id: str) -> object:
        if task_id not in self._store:
            raise TaskNotFoundError(task_id)
        return self._store[task_id]

    def list_by_user(self, user_id: str) -> list[object]:
        return [t for t in self._store.values() if t.user_id == user_id]


class _InMemoryEventRepo:
    def __init__(self) -> None:
        self._logs: dict[str, list[TaskEvent]] = {}

    def append(self, event: TaskEvent) -> TaskEvent:
        log = self._logs.setdefault(event.task_id, [])
        expected = (log[-1].seq if log else None)
        if event.seq != next_seq(expected):
            raise ValueError(f"seq invariant violated: got {event.seq}, expected {next_seq(expected)}")
        import copy
        log.append(copy.deepcopy(event))
        return event

    def load_events(self, task_id: str, after_seq: int = 0) -> list[TaskEvent]:
        log = self._logs.get(task_id, [])
        return [e for e in log if e.seq > after_seq]

    def latest_seq(self, task_id: str):
        log = self._logs.get(task_id)
        return log[-1].seq if log else None

    def truncate(self, task_id: str, after_seq: int) -> None:
        log = self._logs.get(task_id, [])
        self._logs[task_id] = [e for e in log if e.seq <= after_seq]


# --- protocol structural checks ---------------------------------------------

def test_task_repo_is_protocol():
    assert issubclass(TaskRepo, type) is False or TaskRepo.__class__.__name__ == "_ProtocolMeta"
    # runtime-checkable: instances of a structurally-matching class pass isinstance
    assert isinstance(_InMemoryTaskRepo(), TaskRepo)


def test_task_event_repo_is_protocol():
    assert isinstance(_InMemoryEventRepo(), TaskEventRepo)


# --- TaskRepo contract ------------------------------------------------------

def _real_task(tid: str):
    from agentclaw.community.core.task.domain.models import Task
    spec = TaskSpec(metadata=TaskSpecMetadata(id=tid, title="x"))
    return Task(id=tid, user_id="u1", source=TaskSource.API, spec=spec)


def test_task_repo_save_get_roundtrip():
    repo = _InMemoryTaskRepo()
    task = _real_task("t1")
    repo.save(task)
    got = repo.get_by_id("t1")
    assert got.id == "t1"
    assert got.user_id == "u1"
    assert got.source is TaskSource.API


def test_task_repo_save_with_execution_graph_roundtrip():
    repo = _InMemoryTaskRepo()
    from agentclaw.community.core.task.domain.models import Task
    task = Task(
        id="t2",
        user_id="u1",
        source=TaskSource.API,
        spec=TaskSpec(metadata=TaskSpecMetadata(id="t2", title="x")),
        execution_graph=TaskExecutionGraph(
            status=GraphStatus.RUNNING,
            nodes=[Node(node_id="n1", spec="s", status=NodeStatus.RUNNING)],
        ),
    )
    repo.save(task)
    got = repo.get_by_id("t2")
    assert got.execution_graph is not None
    assert got.execution_graph.nodes[0].node_id == "n1"
    assert got.execution_graph.nodes[0].status is NodeStatus.RUNNING


def test_task_repo_get_missing_raises():
    repo = _InMemoryTaskRepo()
    with pytest.raises(TaskNotFoundError):
        repo.get_by_id("nope")


def test_task_repo_list_by_user():
    repo = _InMemoryTaskRepo()
    repo.save(_real_task("t1"))
    repo.save(_real_task("t2"))
    repo.save(_real_task("t3"))
    res = repo.list_by_user("u1")
    assert {t.id for t in res} == {"t1", "t2", "t3"}


def test_task_repo_save_is_deep_copy():
    repo = _InMemoryTaskRepo()
    task = _real_task("t1")
    repo.save(task)
    task.id = "mutated"
    got = repo.get_by_id("t1")
    assert got.id == "t1"  # mutation did not leak into store


# --- TaskEventRepo contract -------------------------------------------------

def test_event_repo_append_assigns_monotonic_seq():
    repo = _InMemoryEventRepo()
    e1 = repo.append(TaskCreated(task_id="t1", seq=1, title="x", source="im"))
    e2 = repo.append(TaskCreated(task_id="t1", seq=2, title="y", source="im"))
    assert e1.seq == 1 and e2.seq == 2


def test_event_repo_append_rejects_non_monotonic_seq():
    repo = _InMemoryEventRepo()
    repo.append(TaskCreated(task_id="t1", seq=1, title="x", source="im"))
    with pytest.raises(ValueError):
        repo.append(TaskCreated(task_id="t1", seq=3, title="gap", source="im"))


def test_event_repo_load_events_sorted_by_seq_after_filter():
    repo = _InMemoryEventRepo()
    for s in (1, 2, 3, 4):
        repo.append(TaskCreated(task_id="t1", seq=s, title=f"t{s}", source="im"))
    tail = repo.load_events("t1", after_seq=2)
    assert [e.seq for e in tail] == [3, 4]


def test_event_repo_load_events_empty_task():
    repo = _InMemoryEventRepo()
    assert repo.load_events("nope") == []


def test_event_repo_per_task_independent_seqs():
    repo = _InMemoryEventRepo()
    repo.append(TaskCreated(task_id="t1", seq=1, title="x", source="im"))
    repo.append(TaskCreated(task_id="t2", seq=1, title="x", source="im"))
    assert len(repo.load_events("t1")) == 1
    assert len(repo.load_events("t2")) == 1


def test_event_not_found_error_is_value_error():
    assert issubclass(EventNotFoundError, ValueError)


def test_task_not_found_error_is_value_error():
    assert issubclass(TaskNotFoundError, ValueError)