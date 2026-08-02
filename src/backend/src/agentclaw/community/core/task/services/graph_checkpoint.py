"""graph_checkpoint — 回溯 / 断点重跑 / 回滚(plan §8.3,FR-GRAPH-03c,tasks T-14)。

事件日志是时间旅行源(append-only,monotonic seq);``GraphSnapshot`` 是物化 fold
缓存。三操作:

- :meth:`snapshot` — 落当前 fold@seq 的物化快照(深拷贝图 + state),存内存快照表。
- :meth:`replay` — 从快照@from_seq 增量重放事件 (from_seq, to_seq] → 重构 graph。
  只读(不改 live task/日志)。用于校验 fold 决定性(U-snapshot-replay)。
- :meth:`rollback` — 截断日志 seq > to_seq(:meth:`TaskEventRepo.truncate`)+ 从最近
  ≤ to_seq 的快照重放到 to_seq 重算 fold + 存(U-rollback)。

seq 单调:replay 校验事件严格递增,disorder 抛 ``IllegalEventError``(U-seq-monotonic)。

注:图**结构**(nodes/edges)由 ``spawn_build_dag``/``add_node`` 物化入图(非全事件溯源);
故 replay 从**结构快照**起步,叠加状态变更事件(NODE_RUNNING/ACCEPTED/...)重放——与
事件溯源 "snapshot + delta" 标准模式一致。结构快照由 ``snapshot`` 深拷贝捕获。
"""
from __future__ import annotations

import copy
from typing import Optional

from agentclaw.community.core.task.domain.events import IllegalEventError
from agentclaw.community.core.task.domain.models import GraphSnapshot, TaskExecutionGraph

try:  # Optional[Task] type-only
    from agentclaw.community.core.task.domain.models import Task  # noqa: F401
except ImportError:  # pragma: no cover
    pass


class GraphCheckpoint:
    """回溯/重跑/回滚门面。宿主提供 TaskService(:meth:`get`/``_apply_event``)、
    :class:`TaskEventRepo`(:meth:`load_events`/:meth:`truncate`)、``TaskRepo``(:meth:`save`)。"""

    def __init__(self, task_service, event_repo, task_repo) -> None:
        self._svc = task_service
        self._events = event_repo
        self._tasks = task_repo
        # (task_id, at_seq) → GraphSnapshot 内存快照表(生产可换持久层)
        self._snapshots: dict[tuple[str, int], GraphSnapshot] = {}

    # --- snapshot ---------------------------------------------------------

    def snapshot(self, task_id: str, at_seq: Optional[int] = None) -> GraphSnapshot:
        """落 fold@seq 物化快照(深拷贝图 + state),存表并返回。"""
        task = self._svc.get(task_id)
        seq = at_seq if at_seq is not None else int(task.latest_event_seq or 0)
        graph = copy.deepcopy(task.execution_graph) if task.execution_graph is not None else None
        if graph is None:
            raise IllegalEventError(f"task {task_id} has no graph to snapshot")
        snap = GraphSnapshot(task_id=task_id, at_seq=seq, graph=graph, taken_at="")
        self._snapshots[(task_id, seq)] = snap
        return snap

    def get_snapshot(self, task_id: str, at_seq: int) -> Optional[GraphSnapshot]:
        return self._snapshots.get((task_id, at_seq))

    # --- replay(只读)-----------------------------------------------------

    def replay(
        self,
        task_id: str,
        from_seq: int,
        to_seq: Optional[int] = None,
    ) -> TaskExecutionGraph:
        """从快照@from_seq 增量重放事件 (from_seq, to_seq] → 重构 graph(只读)。

        events 须严格递增,否则抛 :class:`IllegalEventError`(U-seq-monotonic)。"""
        snap = self._snapshots.get((task_id, from_seq))
        if snap is None:
            raise IllegalEventError(
                f"no snapshot@seq={from_seq} for task {task_id}; call snapshot() first"
            )
        events = self._events.load_events(task_id, from_seq)
        if to_seq is not None:
            events = [e for e in events if e.seq <= to_seq]
        self._assert_monotonic(events)
        task = self._svc.get(task_id)
        task.execution_graph = copy.deepcopy(snap.graph)
        for e in sorted(events, key=lambda ev: ev.seq):
            self._svc._apply_event(task, e.kind, e.payload)  # noqa: SLF001 — 同包 fold
        return task.execution_graph

    # --- rollback(截断 + 重算 + 存)--------------------------------------

    def rollback(self, task_id: str, to_seq: int) -> None:
        """回到 k=to_seq 时刻:截断日志 > to_seq + 从最近 ≤ to_seq 快照重放重算 fold + 存。

        无可用快照时,仅截断日志并把 ``latest_event_seq`` 校正到 to_seq(结构沿用当前 fold)。"""
        self._events.truncate(task_id, to_seq)
        snap_seq = self._latest_snapshot_seq_lte(task_id, to_seq)
        task = self._svc.get(task_id)
        if snap_seq is not None:
            snap = self._snapshots[(task_id, snap_seq)]
            task.execution_graph = copy.deepcopy(snap.graph)
            events = [e for e in self._events.load_events(task_id, snap_seq) if e.seq <= to_seq]
            self._assert_monotonic(events)
            for e in sorted(events, key=lambda ev: ev.seq):
                self._svc._apply_event(task, e.kind, e.payload)  # noqa: SLF001
        task.latest_event_seq = to_seq
        if task.execution_graph is not None:
            task.execution_graph.root_phase = task.status
        self._tasks.save(task)

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _assert_monotonic(events) -> None:
        last = 0
        for e in events:
            if e.seq <= last:
                raise IllegalEventError(
                    f"event seq not strictly increasing: prev={last}, got={e.seq}"
                )
            last = e.seq

    def _latest_snapshot_seq_lte(self, task_id: str, to_seq: int) -> Optional[int]:
        candidates = [s for (tid, s) in self._snapshots if tid == task_id and s <= to_seq]
        return max(candidates) if candidates else None


__all__ = ["GraphCheckpoint"]