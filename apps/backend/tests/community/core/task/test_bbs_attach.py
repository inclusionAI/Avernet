"""BBS 接力步④ attach_bbs_node(scoped bbs 子节点 + start)单测。对齐 task-5 brief(TDD RED→GREEN)。

owner 校验 + 深度闸 BBS_MAX_DEPTH + run_mode=bbs 子节点 PENDING→RUNNING + bbs_relay_count++。
"""
import uuid

import pytest

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    Status,
    TaskGraphPatch,
    TaskInfo,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


def _ti(tid):
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=tid, title="t", instruction="i"),
            context=Context(background="", extend_props={}),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
        execution_config={},
    )


def _scoped_spec():
    return TaskSpec(
        metadata=Metadata(
            task_id=f"bbs-{uuid.uuid4().hex[:6]}", title="bbs-scoped", instruction="do part"
        ),
        context=Context(background="", extend_props={}),
        goal=Goal(objective="part", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
    )


def _bbs_root_planning(svc, tid):
    """构造 bbs_mode + 根 PLANNING(可委托)的可接任务。

    NOTE: brief 原稿用 ``update_task_node_info(status=PLANNING)`` 翻根态,但
    ``PENDING→PLANNING`` 不在 ``_DIRECT_TRANSITIONS``(PENDING 仅可至 RUNNING/HUNG/DONE),
    会抛 ``TaskStateError`` 无法构造可 claim 态。此处采用白盒直改(与 task-3 用
    ``_require_graph`` 一致),单测无态机校验。
    """
    svc.initialize_graph(_ti(tid))
    svc.update_task_graph_info(tid, TaskGraphPatch(extend_props_patch={"bbs_mode": True}))
    graph = svc._require_graph(tid)
    graph.tasks[0].status = Status.PLANNING


def test_attach_creates_bbs_node_running():
    svc = TaskGraphService()
    _bbs_root_planning(svc, "a1")
    svc.claim_bbs_owner("a1", "botA")
    node = svc.attach_bbs_node("a1", parent_node_id="a1", task_spec=_scoped_spec(), bot_id="botA")
    assert node.run_info.run_mode == "bbs"
    assert node.run_info.assignee == "botA"
    assert node.run_info.start_time is not None
    assert node.run_info.extend_props["bbs_claim_at"] == node.run_info.start_time
    # 返回引用即 add_task_nodes 挂入图并被 update_task_node_info 原地翻 RUNNING 的同一对象;
    # 若未来实现改为返回副本,这里改经 query_task_dashboard 重取断言亦可。
    assert node.status == Status.RUNNING
    graph = svc.query_task_dashboard("a1")
    assert graph.extend_props.get("bbs_relay_count") == 1


def test_attach_rejects_non_owner():
    svc = TaskGraphService()
    _bbs_root_planning(svc, "a2")
    svc.claim_bbs_owner("a2", "botA")
    with pytest.raises(TaskStateError):
        svc.attach_bbs_node("a2", "a2", _scoped_spec(), bot_id="botB")


def test_attach_depth_gate_hung():
    svc = TaskGraphService()
    _bbs_root_planning(svc, "a3")
    svc.update_task_graph_info(
        "a3", TaskGraphPatch(extend_props_patch={"execution_config": {"BBS_MAX_DEPTH": 1}})
    )
    svc.claim_bbs_owner("a3", "botA")
    svc.attach_bbs_node("a3", "a3", _scoped_spec(), "botA")  # relay_count 0→1 == BBS_MAX_DEPTH 1
    # 清 owner 后第二次 attach 应触发深度闸(relay_count 1 >= BBS_MAX_DEPTH 1 → HUNG)
    svc.update_task_node_info(
        TaskNodePatch(task_id="a3", node_id="a3", extend_props_patch={"bbs_owner": None})
    )
    svc.claim_bbs_owner("a3", "botA")
    with pytest.raises(TaskStateError):
        svc.attach_bbs_node("a3", "a3", _scoped_spec(), "botA")
    assert svc.query_task_dashboard("a3").status == Status.HUNG


def test_facade_attach_bbs_node_delegates():
    """TaskService.attach_bbs_node facade 委托到 TaskGraphService.attach_bbs_node。"""
    graph = TaskGraphService()
    svc = TaskService(graph)
    _bbs_root_planning(graph, "a4")
    svc.claim_bbs_task("a4", "botA")
    node = svc.attach_bbs_node("a4", "a4", _scoped_spec(), "botA")
    assert node.run_info.run_mode == "bbs"
    assert node.status == Status.RUNNING


def test_protocol_has_attach_bbs_node_signature():
    """TaskServiceProtocol 声明 attach_bbs_node 签名(runtime_checkable Protocol)。"""
    assert hasattr(TaskServiceProtocol, "attach_bbs_node")
