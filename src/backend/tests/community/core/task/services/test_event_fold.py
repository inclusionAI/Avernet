"""v2 判定节点 fold + BBS 确认/cancel 通道 on_event 测试(tasks T-23/T-24,plan §5.2/§12A/§13)。

覆盖:
- ``EXEC_AGGREGATED`` fold(pass→DONE / fail→FAILED,节点 + SubtaskState)
- ``NODE_HANG`` fold(node→HUNG + graph→HUMAN_REQUIRED)
- ``BBS_CONFIRMED`` 确认升 BBS(HUMAN_REQUIRED→BBS_ACTIVE;BBS 为任务级模式,不落节点)
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
    RunMode,
    SubTaskSpec,
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
    """建 v2 动作图 task 并推进到 RUNNING,带一个 EXEC_AGGREGATE 节点供判验 fold。

    create→clarify(confirmed)走 DRAFTING→DEFINED;mark_graph_status DEFINED→RUNNING;
    init_execution_graph 落 recognition/clarify/execute_start(已 DONE)+ 根 BOT_SEARCH。"""
    t = svc.create(title="t", background="obj")
    svc.clarify(t.id, {}, confirmed=True)
    task = svc.get(t.id)
    svc.mark_graph_status(task, GraphStatus.RUNNING)
    svc.init_execution_graph(task)
    task = svc.get(task.id)
    # 落一个 EXEC_AGGREGATE 节点(挂 n_bot_search)
    svc.add_node(task.id, SubTaskSpec(node_id="n_agg", spec="exec-aggregate"), "n_bot_search", NodeType.EXEC_AGGREGATE)
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
    assert task.execution_graph.status is GraphStatus.RUNNING
    svc.on_event(_ev(task.id, EventKind.NODE_HANG, next_seq(0), node_id="n_bot_search"))
    task = svc.get(task.id)
    assert task.execution_graph.status is GraphStatus.HUMAN_REQUIRED
    node = svc._find_node(task, "n_bot_search")  # noqa: SLF001
    assert node.status is NodeStatus.HUNG


# --- BBS_CONFIRMED / HANG_CANCELLED 通道(T-24)-----------------------------


def test_bbs_confirmed_channel_escalates_to_bbs_active():
    svc = _service()
    task = _task_on_plaza(svc)
    # 先 hang 到 HUMAN_REQUIRED
    svc.on_event(_ev(task.id, EventKind.NODE_HANG, next_seq(0), node_id="n_bot_search", hang=True))
    task = svc.get(task.id)
    assert task.execution_graph.status is GraphStatus.HUMAN_REQUIRED
    # 人确认升 BBS → BBS_ACTIVE(任务级模式,bots 读 State 自驱剩余子任务,不落节点)
    svc.on_event(_ev(task.id, EventKind.BBS_CONFIRMED, next_seq(1), node_id="n_bot_search"))
    task = svc.get(task.id)
    assert task.execution_graph.status is GraphStatus.BBS_ACTIVE
    assert task.status is GraphStatus.BBS_ACTIVE


def test_hang_cancelled_channel_routes_to_failed_terminal():
    svc = _service()
    task = _task_on_plaza(svc)
    svc.on_event(_ev(task.id, EventKind.NODE_HANG, next_seq(0), node_id="n_bot_search", hang=True))
    # 人确认不升 → FAILED 终态
    svc.on_event(_ev(task.id, EventKind.HANG_CANCELLED, next_seq(1)))
    task = svc.get(task.id)
    assert task.status is GraphStatus.FAILED


# --- 三终止分支(U-three-terminals,T-23)------------------------------------


def test_three_terminals_aggregate_pass_done():
    # 终止①:聚合 PASS → goal-verify PASS → DONE
    svc = _service()
    task = _task_on_plaza(svc)
    svc.on_event(_ev(task.id, EventKind.EXEC_AGGREGATED, next_seq(0), node_id="n_agg", verdict="pass"))
    # goal-verify 发生在 REVIEWING 阶段(scheduler 推进后)
    task = svc.get(task.id)
    task.status = GraphStatus.REVIEWING
    svc._task_repo.save(task)  # noqa: SLF001
    svc.on_event(_ev(task.id, EventKind.GOAL_VERIFIED, next_seq(1), node_id="n_agg", verdict="pass"))
    task = svc.get(task.id)
    assert task.status is GraphStatus.DONE
    assert task.execution_graph.status is GraphStatus.DONE


def test_three_terminals_hang_escalate_bbs():
    # 终止②:hang → 人确认升 BBS → BBS_ACTIVE 同图延续(非终态)
    svc = _service()
    task = _task_on_plaza(svc)
    svc.on_event(_ev(task.id, EventKind.NODE_HANG, next_seq(0), node_id="n_bot_search", hang=True))
    svc.on_event(_ev(task.id, EventKind.BBS_CONFIRMED, next_seq(1), node_id="n_bot_search"))
    task = svc.get(task.id)
    assert task.execution_graph.status is GraphStatus.BBS_ACTIVE
    assert task.status is not GraphStatus.FAILED  # 同图延续,非终态


def test_three_terminals_hang_cancel_failed():
    # 终止③:hang → 人确认不升 → FAILED
    svc = _service()
    task = _task_on_plaza(svc)
    svc.on_event(_ev(task.id, EventKind.NODE_HANG, next_seq(0), node_id="n_bot_search", hang=True))
    svc.on_event(_ev(task.id, EventKind.HANG_CANCELLED, next_seq(1)))
    assert svc.get(task.id).status is GraphStatus.FAILED


# --- NODE_RELEASED fold(BBS 让出/兜底,Task 1)------------------------------

def _running_claimed_node(svc):
    """建一个已被 bot-A claim 的 RUNNING 节点 n1,供 NODE_RELEASED fold 测试。"""
    t = svc.create(title="t")
    svc.clarify(t.id, {}, confirmed=True)          # DRAFTING → DEFINED
    task = svc.get(t.id)
    svc.init_execution_graph(task)                 # 落 recognition/clarify/execute_start + 根
    svc.add_node(
        task.id,
        SubTaskSpec(node_id="n1", spec="a", run_mode=RunMode.BBS),
        "n_execute_start",
        NodeType.DISPATCH,
    )
    svc.claim_node(t.id, "n1", "bot-A")            # PENDING → RUNNING + assignee=bot-A
    return t.id, "n1"


def test_node_released_fold_running_to_failed_no_escalation():
    svc = _service()
    task_id, node_id = _running_claimed_node(svc)
    before = svc.get(task_id).execution_graph.status
    svc.on_event({"task_id": task_id, "kind": "node.released",
                  "payload": {"node_id": node_id, "outcome": "handoff"}})
    task = svc.get(task_id)
    node = next(n for n in task.execution_graph.nodes if n.node_id == node_id)
    assert node.status is NodeStatus.FAILED
    assert node.assignee is None
    assert node.properties.get("release_outcome") == "handoff"
    assert task.execution_graph.status is before  # 不升人工:graph 状态不变