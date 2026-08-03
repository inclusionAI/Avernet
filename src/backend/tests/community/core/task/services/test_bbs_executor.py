"""TDD for BbsExecutor (Phase 5, plan §5 / Case B/C/E).

Shared blackboard = TaskExecutionGraph.广场 bots read via TaskService query face
and write via on_event (no Scheduler tick — BBS is self-drive). Claim is CAS
(first bot wins; second gets None on the same node). BBS goal-FAIL parks the
graph at AWAITING_HUMAN_ACCEPT (task-level HUNG is gone — spec §2).
"""
from __future__ import annotations


from agentclaw.community.core.task.protocols import DispatchResult
from agentclaw.community.core.task.domain.events import EventKind, TaskEvent, next_seq
from agentclaw.community.core.task.domain.models import NodeStatus, RunMode
from agentclaw.community.core.task.domain.models import (
    NodeType,
    SubTaskSpec,
    GraphStatus,
)
from agentclaw.community.core.task.services import BbsExecutorService, TaskService
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)


def _service() -> tuple[TaskService, BbsExecutorService]:
    svc = TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher(), None)
    return svc, BbsExecutorService(svc)


def _planned_with_dag(svc: TaskService, nodes=("n1", "n2")) -> str:
    t = svc.create(title="t")
    svc.clarify(t.id, {"summary": "s"})
    svc.clarify(t.id, {}, confirmed=True)
    task = svc.get(t.id)
    svc.init_execution_graph(task)
    # 2026-08-03:Plan 退场,DISPATCH 节点不再由 plan 预拆 → 显式 add_node(供 BBS claim)
    for nid in nodes:
        svc.add_node(
            t.id,
            SubTaskSpec(node_id=nid, spec=nid, run_mode=RunMode.SINGLE_BOT),
            "n_execute_start",
            NodeType.DISPATCH,
        )
    task = svc.get(t.id)
    # root BOT_SEARCH 标 DONE,免 BBS claim 误抢根节点(应认领 n1/n2 DISPATCH 叶子)
    root = svc._find_node(task, "n_bot_search")  # noqa: SLF001
    if root is not None:
        root.status = NodeStatus.DONE
        st_root = task.execution_graph.state.subtasks.get("n_bot_search")
        if st_root is not None:
            st_root.status = NodeStatus.DONE
        svc._task_repo.save(task)  # noqa: SLF001
    # advance to EXECUTING + plaza (BBS广场 operates on a plaza graph)
    task.status = GraphStatus.RUNNING
    task.execution_graph.status = GraphStatus.RUNNING
    svc._task_repo.save(task)  # noqa: SLF001
    return t.id


# --- claim CAS (Case B) -----------------------------------------------------


def test_claim_first_bot_wins_second_gets_none_on_same_node():
    svc, bbs = _service()
    tid = _planned_with_dag(svc, nodes=("n1",))
    first = bbs.claim(tid, "bot-a")
    assert isinstance(first, DispatchResult)
    assert first.executor_id == "bot-a"
    assert first.run_mode is RunMode.BBS
    # node is now RUNNING — second bot gets None (no other PENDING node)
    second = bbs.claim(tid, "bot-b")
    assert second is None


def test_claim_advances_to_next_pending_node_when_one_taken():
    svc, bbs = _service()
    tid = _planned_with_dag(svc, nodes=("n1", "n2"))
    first = bbs.claim(tid, "bot-a")  # claims n1 (no edges → both unlocked)
    assert first is not None
    assert first.executor_id == "bot-a"
    # second bot advances to the next PENDING node (n2), does NOT re-claim n1
    second = bbs.claim(tid, "bot-b")
    assert second is not None
    assert second.executor_id == "bot-b"
    g = svc.get_task_graph(tid)
    n1 = next(n for n in g["nodes"] if n["node_id"] == "n1")
    n2 = next(n for n in g["nodes"] if n["node_id"] == "n2")
    assert n1["assignee"] == "bot-a"
    assert n2["assignee"] == "bot-b"


def test_claim_unknown_task_returns_none():
    svc, bbs = _service()
    assert bbs.claim("ghost", "bot-a") is None


# --- shared blackboard read/write (Case C) ---------------------------------


def test_post_progress_folds_event_into_blackboard():
    svc, bbs = _service()
    tid = _planned_with_dag(svc, nodes=("n1",))
    bbs.claim(tid, "bot-a")  # n1 → RUNNING
    # bot reports acceptance via广场续做
    seq = next_seq(svc._event_repo.latest_seq(tid))  # noqa: SLF001
    bbs.post_progress(
        TaskEvent(task_id=tid, seq=seq, kind=EventKind.NODE_ACCEPTED, payload={"node_id": "n1", "verifier": "bot-a"})
    )
    # shared blackboard reflects the fold
    g = svc.get_task_graph(tid)
    n1 = next(n for n in g["nodes"] if n["node_id"] == "n1")
    assert n1["status"] == "done"


def test_get_task_graph_is_the_shared_blackboard_for_all_bots():
    """Case C: every bot读 the same TaskExecutionGraph via the query face."""
    svc, bbs = _service()
    tid = _planned_with_dag(svc, nodes=("n1",))
    bbs.claim(tid, "bot-a")
    # bot-b reads the same blackboard → sees bot-a's claim
    graph_for_b = svc.get_task_graph(tid)
    n1 = next(n for n in graph_for_b["nodes"] if n["node_id"] == "n1")
    assert n1["status"] == "running"
    assert n1["assignee"] == "bot-a"


# --- BBS goal fail → FAILED 终态 (v2 三终止, O-P2/§13 Case E) ----------------


def test_bbs_goal_reject_post_bbs_failed_terminal():
    svc, bbs = _service()
    tid = _planned_with_dag(svc, nodes=("n1",))
    # v2:BBS 后 goal-FAIL → FAILED 终态(不回环/不再上升;spec §13)。
    task = svc.get(tid)
    task.status = GraphStatus.BBS_ACTIVE
    task.execution_graph.status = GraphStatus.BBS_ACTIVE
    svc._task_repo.save(task)  # noqa: SLF001
    seq = next_seq(svc._event_repo.latest_seq(tid))  # noqa: SLF001
    bbs.post_progress(
        TaskEvent(
            task_id=tid,
            seq=seq,
            kind=EventKind.GOAL_REJECTED,
            payload={"verifier": "bbs", "verdict": "fail", "reason": "plaza stuck", "run_mode": "bbs"},
        )
    )
    final = svc.get(tid)
    assert final.status is GraphStatus.FAILED  # BBS 后终态


def test_post_progress_unknown_task_returns_none():
    svc, bbs = _service()
    assert bbs.post_progress(TaskEvent(task_id="ghost", seq=1, kind=EventKind.NODE_RUNNING)) is None


def test_retrieve_state_reads_shared_blackboard():
    # §18.1-10:progress_snapshot 不存在;广场 bot 读经 retrieve_state。
    from agentclaw.community.core.task.domain.models import StateSemantics

    svc, bbs = _service()
    tid = _planned_with_dag(svc, nodes=("n1",))
    out = bbs.retrieve_state(tid, None)
    assert out["scope"] == "public"
    assert "public" in out
    # 新设计:init_execution_graph 为每个节点建 SubtaskState 分区 → n1 分区已存在(空执行上下文)。
    out_n1 = bbs.retrieve_state(tid, "n1")
    assert out_n1["subtask"]["node_id"] == "n1"
    assert out_n1["subtask"]["execution_context"] == {}
    # 写分区后可读(广场 bot 读执行上下文)
    svc.update_state(tid, "n1", {"execution_context": {"k": "v"}}, StateSemantics.MERGE)
    out_n1b = bbs.retrieve_state(tid, "n1")
    assert out_n1b["subtask"]["node_id"] == "n1"
    assert out_n1b["subtask"]["execution_context"] == {"k": "v"}