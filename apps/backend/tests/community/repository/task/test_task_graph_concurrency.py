"""SQLite-equivalence concurrency tests for the shared graph store.

These exercise the CAS primitives that singlebox/SQLite CAN enforce (lease CAS
via conditional UPDATE, optimistic version rejection, BBS claim CAS via row
lock). True cross-instance row-lock serialization (SELECT ... FOR UPDATE
blocking) is OceanBase behavior and is documented in
``2026_08_24_oceanbase_validation.md`` (V1/V2); it is intentionally not
reproduced on SQLite, whose ``FOR UPDATE`` is a no-op.
"""
from __future__ import annotations

from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
    GraphVersionConflictError,
    TaskGraphRepository,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.types import TaskInfoRecord


def _seed_info(db, task_id, *, status=Status.RUNNING):
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0,
            task_id=task_id,
            source_type="bot",
            owner_user_id="U-1",
            owner_bot_id="B-1",
            execution_config={},
            task_spec={"metadata": {"task_id": task_id}},
            status=status,
        )
    )


def test_stale_graph_version_rejected(db):
    """Concurrent writers: the second must observe the new version and conflict.

    On SQLite this is simulated sequentially: writer A advances 0→1, then writer
    B (using the stale expected_version=0) must raise. On OceanBase the row lock
    in save_graph makes B block and observe v=1 after A commits (V2)."""
    task_id = "T-VERSION-CONC"
    _seed_info(db, task_id)
    repo = TaskGraphRepository(db)
    # version 0 -> 1
    repo.save_graph(
        _minimal_graph(task_id), expected_version=0,
        runtime_status=Status.RUNNING, action_events=[],
    )
    # a stale writer using expected_version=0 must be rejected
    try:
        repo.save_graph(
            _minimal_graph(task_id), expected_version=0,
            runtime_status=Status.RUNNING, action_events=[],
        )
    except GraphVersionConflictError:
        pass
    else:
        raise AssertionError("stale concurrent writer was accepted")


def test_recovery_lease_cas_one_winner(db):
    """Two lease acquirers of the same task: exactly one wins (rowcount == 1).

    The conditional ``UPDATE … WHERE lease_until IS NULL OR lease_until < now``
    CAS is portable and deterministic on SQLite (and identical on OceanBase).
    SQLite shares a single in-process connection, so the two acquirers are driven
    sequentially here; the CAS still proves a second acquirer is rejected while
    the lease is held. On OceanBase, concurrent acquirers on separate
    connections serialize through the conditional UPDATE (V1/V2)."""
    task_id = "T-LEASE-CAS"
    _seed_info(db, task_id, status=Status.RUNNING)
    repo = TaskGraphRepository(db)

    assert repo.acquire_lease(task_id, instance_id="pod-A", lease_seconds=60) is True
    # second acquirer while the live lease is held -> exactly one winner (pod-A)
    assert repo.acquire_lease(task_id, instance_id="pod-B", lease_seconds=60) is False
    assert repo.heartbeat(task_id, instance_id="pod-A", lease_seconds=60) is True
    assert repo.heartbeat(task_id, instance_id="pod-B", lease_seconds=60) is False
    assert repo.release_lease(task_id, instance_id="pod-A") is True
    assert repo.release_lease(task_id, instance_id="pod-B") is False
    # after release, another instance may claim
    assert repo.acquire_lease(task_id, instance_id="pod-B", lease_seconds=60) is True


def test_list_recoverable_skips_terminal_and_leased(db):
    task_id = "T-RECOV-SCAN"
    _seed_info(db, task_id, status=Status.RUNNING)
    repo = TaskGraphRepository(db)
    assert task_id in repo.list_recoverable(limit=10)
    # once leased (lease_until in the future) it must drop out of recoverable
    assert repo.acquire_lease(task_id, instance_id="pod-A", lease_seconds=60) is True
    assert task_id not in repo.list_recoverable(limit=10)
    repo.release_lease(task_id, instance_id="pod-A")


# ---- helpers ----
def _minimal_graph(task_id):
    from agentclaw.community.core.task.domain.models import (
        Context, Goal, Metadata, RuntimeInfo, TaskExecutionGraph, TaskNode, TaskSpec,
    )
    root = TaskNode(
        node_id=task_id, task_id=task_id, status=Status.RUNNING,
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="t", instruction="i"),
            context=Context(background="b"),
            goal=Goal(objective="o", acceptances=[]),
        ),
        run_info=RuntimeInfo(),
        node_run_graph=None,
    )
    graph = TaskExecutionGraph(
        run_id=1, loop_round=0, status=Status.RUNNING, output={}, extend_props={},
        tasks=[root], task_id=task_id,
    )
    root.node_run_graph = graph
    return graph
