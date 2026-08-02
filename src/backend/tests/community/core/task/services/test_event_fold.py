"""v2 判定节点 fold + BBS 确认/cancel 通道 on_event 测试(tasks T-23/T-24,plan §5.2/§12A/§13)。

覆盖:
- ``EXEC_AGGREGATED`` fold(pass→DONE / fail→FAILED,节点 + SubtaskState)
- ``NODE_HANG`` fold(node→HUMAN_REQUIRED + graph→AWAITING_HUMAN_ACCEPT)
- ``BBS_CONFIRMED`` 确认升 BBS(AWAITING_HUMAN_ACCEPT→ON_PLAZA + BBS_DISPATCH 节点)
- ``HANG_CANCELLED`` 不升 → FAILED 终态
- 三终止分支(U-three-terminals)
经真实 ``TaskService.on_event`` fold(POST /events 通道已在 router 暴露)。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.events import EventKind, next_seq
from agentclaw.community.core.task.domain.models import (
    GraphStatus,
    NodeStatus,
    NodeType,
    Plan,
    SubTaskSpec,
    TaskStatus,
)
from agentclaw.community.core.task.services import TaskService
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)


def _service() -> TaskService:
    return TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())


def _task_on_plaza(svc: TaskService, acceptances: int = 1):
    """建 v2 动作图 task + ON_PLAZA + EXECUTING,带一个 EXEC_AGGREGATE 节点供判验 fold。

    空 plan(sub_tasks=[]) → spawn_build_dag 走根 BOT_SEARCH(n_bot_search);spawn 自持久化。"""
    t = svc.create(title="t", background="obj")
    task = svc.get(t.id)
    svc.finalize_plan(task.id, Plan(sub_tasks=[], confidence=0.9))
    task = svc.get(task.id)
    task.status = TaskStatus.EXECUTING
    task.execution_graph = None
    svc._task_repo.save(task)
    svc.spawn_build_dag(task)
    task = svc.get(task.id)
    svc.mark_graph_status(task, GraphStatus.ON_PLAZA)
    # 落一个 EXEC_AGGREGATE 节点(挂 n_bot_search)
    svc.add_node(task.id, SubTaskSpec(node_id="n_agg", spec="exec-aggregate"), "n_bot_search", NodeType.EXEC_AGGREGATE)
    task = svc.get(task.id)
    # 起 EXECUTING(root_phase 同步)
    task.execution_graph.root_phase = TaskStatus.EXECUTING
    svc._task_repo.save(task)
    return svc.get(task.id)


def _ev(task_id, kind, seq, **payload):
    return {"task_id": task_id, "kind": kind, "seq": seq, "payload": dict(payload)}


# --- EXEC_AGGREGATED fold (T-23) -------------------------------------------


def test_exec_aggregated_pass_marks_done():
    svc = _service()
    task = _task_on_plaza(svc)
    svc.on_event(_ev(task.id, EventKind.EXEC_AGGREGATED, next_seq(0), node_id="n_agg", verdict="pass"))
    task = svc.get(task.id)
    node = svc._find_node(task, "n_agg")  # noqa: SLF001
    assert node.status is NodeStatus.DONE
    assert node.properties["acceptance_result"] == "pass"
    assert task.execution_graph.state.subtasks["n_agg"].status is NodeStatus.DONE


def test_exec_aggregated_fail_marks_failed():
    svc = _service()
    task = _task_on_plaza(svc)
    svc.on_event(_ev(task.id, EventKind.EXEC_AGGREGATED, next_seq(0), node_id="n_agg", verdict="fail"))
    task = svc.get(task.id)
    node = svc._find_node(task, "n_agg")  # noqa: SLF001
    assert node.status is NodeStatus.FAILED
    assert node.properties["acceptance_result"] == "fail"
    assert task.execution_graph.state.subtasks["n_agg"].status is NodeStatus.FAILED


# --- NODE_HANG fold (T-23) -------------------------------------------------


def test_node_hang_parks_awaiting_human_accept():
    svc = _service()
    task = _task_on_plaza(svc)
    assert task.execution_graph.graph_status is GraphStatus.ON_PLAZA
    svc.on_event(_ev(task.id, EventKind.NODE_HANG, next_seq(0), node_id="n_bot_search"))
    task = svc.get(task.id)
    assert task.execution_graph.graph_status is GraphStatus.AWAITING_HUMAN_ACCEPT
    node = svc._find_node(task, "n_bot_search")  # noqa: SLF001
    assert node.status is NodeStatus.HUMAN_REQUIRED


# --- BBS_CONFIRMED / HANG_CANCELLED 通道(T-24)-----------------------------


def test_bbs_confirmed_channel_escalates_on_plaza_and_adds_bbs_node():
    svc = _service()
    task = _task_on_plaza(svc)
    # 先 hang 到 AWAITING_HUMAN_ACCEPT
    svc.on_event(_ev(task.id, EventKind.NODE_HANG, next_seq(0), node_id="n_bot_search", hang=True))
    task = svc.get(task.id)
    assert task.execution_graph.graph_status is GraphStatus.AWAITING_HUMAN_ACCEPT
    # 人确认升 BBS → ON_PLAZA + BBS_DISPATCH 节点(经 POST /events 回投)
    svc.on_event(_ev(task.id, EventKind.BBS_CONFIRMED, next_seq(1), node_id="n_bot_search"))
    task = svc.get(task.id)
    assert task.execution_graph.graph_status is GraphStatus.ON_PLAZA
    assert any(n.node_type is NodeType.BBS_DISPATCH for n in task.execution_graph.nodes)
    # 同图延续(同一 TaskExecutionGraph,无新图)
    bbs = next(n for n in task.execution_graph.nodes if n.node_type is NodeType.BBS_DISPATCH)
    assert bbs.node_id == "n_bot_search_bbs"
    # BBS_DISPATCH 与 hang 节点经 DEPENDENCY 边衔接
    assert any(
        e.from_node == "n_bot_search" and e.to_node == "n_bot_search_bbs"
        for e in task.execution_graph.edges
    )


def test_hang_cancelled_channel_routes_to_failed_terminal():
    svc = _service()
    task = _task_on_plaza(svc)
    svc.on_event(_ev(task.id, EventKind.NODE_HANG, next_seq(0), node_id="n_bot_search", hang=True))
    # 人确认不升 → FAILED 终态
    svc.on_event(_ev(task.id, EventKind.HANG_CANCELLED, next_seq(1)))
    task = svc.get(task.id)
    assert task.status is TaskStatus.FAILED
    assert not any(n.node_type is NodeType.BBS_DISPATCH for n in task.execution_graph.nodes)


# --- 三终止分支(U-three-terminals,T-23)------------------------------------


def test_three_terminals_aggregate_pass_done():
    # 终止①:聚合 PASS → goal-verify PASS → DONE
    svc = _service()
    task = _task_on_plaza(svc)
    svc.on_event(_ev(task.id, EventKind.EXEC_AGGREGATED, next_seq(0), node_id="n_agg", verdict="pass"))
    # goal-verify 发生在 REVIEWING 阶段(scheduler 推进后)
    task = svc.get(task.id)
    task.status = TaskStatus.REVIEWING
    task.execution_graph.root_phase = TaskStatus.REVIEWING
    svc._task_repo.save(task)  # noqa: SLF001
    svc.on_event(_ev(task.id, EventKind.GOAL_VERIFIED, next_seq(1), node_id="n_agg", verdict="pass"))
    task = svc.get(task.id)
    assert task.status is TaskStatus.DONE
    assert task.execution_graph.graph_status is GraphStatus.VERIFIED


def test_three_terminals_hang_escalate_bbs():
    # 终止②:hang → 人确认升 BBS → 同图延续(非终态)
    svc = _service()
    task = _task_on_plaza(svc)
    svc.on_event(_ev(task.id, EventKind.NODE_HANG, next_seq(0), node_id="n_bot_search", hang=True))
    svc.on_event(_ev(task.id, EventKind.BBS_CONFIRMED, next_seq(1), node_id="n_bot_search"))
    task = svc.get(task.id)
    assert task.execution_graph.graph_status is GraphStatus.ON_PLAZA
    assert any(n.node_type is NodeType.BBS_DISPATCH for n in task.execution_graph.nodes)
    assert task.status is not TaskStatus.FAILED  # 同图延续,非终态


def test_three_terminals_hang_cancel_failed():
    # 终止③:hang → 人确认不升 → FAILED
    svc = _service()
    task = _task_on_plaza(svc)
    svc.on_event(_ev(task.id, EventKind.NODE_HANG, next_seq(0), node_id="n_bot_search", hang=True))
    svc.on_event(_ev(task.id, EventKind.HANG_CANCELLED, next_seq(1)))
    assert svc.get(task.id).status is TaskStatus.FAILED