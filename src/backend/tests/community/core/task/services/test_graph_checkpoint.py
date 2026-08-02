"""graph_checkpoint TDD(tasks T-14,plan §8.3/FR-GRAPH-03c)。

U-snapshot-replay / U-rollback / U-seq-monotonic。经真实 ``TaskService.on_event``
落事件 + ``GraphCheckpoint`` 重放/回滚。
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.events import EventKind, IllegalEventError, next_seq
from agentclaw.community.core.task.domain.models import NodeStatus, Plan, SubTaskSpec, TaskStatus
from agentclaw.community.core.task.services import GraphCheckpoint, TaskService
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)


def _service() -> tuple[TaskService, GraphCheckpoint]:
    svc = TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())
    ck = GraphCheckpoint(svc, svc._event_repo, svc._task_repo)  # noqa: SLF001
    return svc, ck


def _planned(svc: TaskService) -> str:
    t = svc.create(title="t")
    svc.finalize_plan(
        t.id,
        Plan(sub_tasks=[SubTaskSpec(node_id="n1", spec="do n1"), SubTaskSpec(node_id="n2", spec="do n2")], confidence=0.8),
    )
    task = svc.get(t.id)
    task.status = TaskStatus.EXECUTING
    task.execution_graph.root_phase = TaskStatus.EXECUTING
    svc._task_repo.save(task)  # noqa: SLF001
    task = svc.get(t.id)
    svc.spawn_build_dag(task)
    svc._task_repo.save(task)  # noqa: SLF001 — spawn_build_dag 只改内存,需存
    return t.id


def _ev(task_id, kind, seq, **payload):
    return {"task_id": task_id, "kind": kind, "seq": seq, "payload": dict(payload)}


# --- U-snapshot-replay -----------------------------------------------------


def test_snapshot_replay_matches_live_fold():
    svc, ck = _service()
    tid = _planned(svc)
    # n1 → RUNNING
    svc.on_event(_ev(tid, EventKind.NODE_RUNNING, next_seq(svc._event_repo.latest_seq(tid)), node_id="n1"))  # noqa: SLF001
    seq_after_running = svc.get(tid).latest_event_seq
    # 落快照@running
    ck.snapshot(tid, seq_after_running)
    # n1 → DONE
    svc.on_event(_ev(tid, EventKind.NODE_ACCEPTED, next_seq(svc._event_repo.latest_seq(tid)), node_id="n1", verifier="bot"))  # noqa: SLF001
    live = svc.get(tid)
    live_n1 = next(n for n in live.execution_graph.nodes if n.node_id == "n1")
    assert live_n1.status is NodeStatus.DONE
    # 增量重放:从快照@running 叠加 (running, now] 事件 → 应与 live fold 一致
    replayed = ck.replay(tid, seq_after_running)
    r_n1 = next(n for n in replayed.nodes if n.node_id == "n1")
    assert r_n1.status is NodeStatus.DONE  # 重放得到与 live 相同的终态


# --- U-rollback ------------------------------------------------------------


def test_rollback_truncates_log_and_recomputes_fold():
    svc, ck = _service()
    tid = _planned(svc)
    svc.on_event(_ev(tid, EventKind.NODE_RUNNING, next_seq(svc._event_repo.latest_seq(tid)), node_id="n1"))  # noqa: SLF001
    seq_running = svc.get(tid).latest_event_seq
    ck.snapshot(tid, seq_running)  # 快照:n1 RUNNING
    svc.on_event(_ev(tid, EventKind.NODE_ACCEPTED, next_seq(svc._event_repo.latest_seq(tid)), node_id="n1", verifier="bot"))  # noqa: SLF001
    svc.get(tid).latest_event_seq
    assert svc.get(tid).execution_graph is not None
    assert next(n for n in svc.get(tid).execution_graph.nodes if n.node_id == "n1").status is NodeStatus.DONE
    # 回滚到 seq_running:n1 应回到 RUNNING,日志截断
    ck.rollback(tid, seq_running)
    task = svc.get(tid)
    assert task.latest_event_seq == seq_running
    n1 = next(n for n in task.execution_graph.nodes if n.node_id == "n1")
    assert n1.status is NodeStatus.RUNNING  # 回滚重算到 running 时刻
    # 日志已截断:load_events after seq_running 应为空
    assert svc._event_repo.load_events(tid, seq_running) == []  # noqa: SLF001


# --- U-seq-monotonic -------------------------------------------------------


def test_seq_monotonic_disorder_raises():
    # replay 校验事件严格递增;重复 / 回退 seq 抛 IllegalEventError
    class _E:
        def __init__(self, s):
            self.seq = s
            self.kind = EventKind.NODE_RUNNING
            self.payload = {}

    with pytest.raises(IllegalEventError):
        GraphCheckpoint._assert_monotonic([_E(2), _E(2)])  # 重复 seq
    with pytest.raises(IllegalEventError):
        GraphCheckpoint._assert_monotonic([_E(3), _E(2)])  # 回退 seq


def test_replay_without_snapshot_raises():
    svc, ck = _service()
    tid = _planned(svc)
    with pytest.raises(IllegalEventError):
        ck.replay(tid, 999)  # 无快照