"""BBS drain 守卫(FR-EXT-06):``_prepare_into`` 跳过 ``run_mode=="bbs"`` PENDING 节点单测。

对齐 task-9 brief(TDD RED→GREEN)。bbs 节点由 bot 经 bbs/attach/bbs/result 自驱
(Tasks 5/7),框架的派发/drain 不得自动消费——否则会给 bbs PENDING 叶置 ``dispatching``
飞行标记并交付 ``_drain`` 翻 RUNNING。本测构造一个 root PLANNING + bbs PENDING 叶(经
``initialize_graph`` + ``add_task_nodes`` 公共 API),直接调 ``_prepare_into(task_id, [])``
断言 bbs 叶未被受理(保持 PENDING、assignee 不变、未置 dispatching)。
"""
import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


def _ti(tid: str) -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=tid, title="t9 root", instruction="root instruction"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="botA",
        execution_config={},
    )


def _bbs_leaf(tid: str) -> TaskNode:
    """构造一个 PENDING 的 run_mode=bbs 叶节点(bot 已自挂前/异常态)。"""
    return TaskNode(
        node_id="bbs-leaf-1",
        task_id=tid,
        status=Status.PENDING,
        task_spec=TaskSpec(
            metadata=Metadata(task_id=tid, title="bbs leaf", instruction="bbs part"),
            context=Context(background="bg"),
            goal=Goal(objective="part", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
        ),
        run_info=RuntimeInfo(run_mode="bbs", assignee="botA"),
        node_run_graph=None,  # type: ignore[arg-type]  store 回填
    )


def _seed_bbs_pending_graph() -> tuple[TaskGraphService, str]:
    """root PLANNING + 一个 PENDING bbs 叶(经公共 API:initialize_graph → add_task_nodes)。

    ``add_task_nodes`` 的 cond_a(单根 PENDING、无 relations)满足,挂入 bbs 叶后根自动进
    PLANNING(委托态)。bbs 叶保持 PENDING/run_mode=bbs/assignee=botA。
    """
    svc = TaskGraphService()
    tid = "t9-bbs-task"
    svc.initialize_graph(_ti(tid))
    svc.add_task_nodes([_bbs_leaf(tid)], parent_node_id=tid)
    return svc, tid


@pytest.mark.asyncio
async def test_bbs_pending_node_not_auto_dispatched():
    """run_mode=bbs 的 PENDING 叶不应被 _prepare_into 纳入派发(不置 dispatching、不翻 RUNNING)。"""
    svc, tid = _seed_bbs_pending_graph()
    engine = ExecutionEngine(svc)  # 无 bot/bcs/discover:守卫使 bbs 叶不被派发,无需真实端口

    side: list[tuple] = []
    await engine._prepare_into(tid, side)  # side 空,仅扫 PENDING 候选

    graph = svc.query_task_dashboard(tid)
    bbs_leaf = next(n for n in graph.tasks if n.run_info.run_mode == "bbs")
    assert bbs_leaf.status == Status.PENDING  # 未被自动翻 RUNNING
    assert bbs_leaf.run_info.assignee == "botA"  # 未被 dispatcher 改写
    assert bbs_leaf.run_info.run_mode == "bbs"  # run_mode 维持 bbs
    # 关键断言(FR-EXT-06):框架未对 bbs 叶置 dispatching 飞行标记(即未被纳入派发)
    assert not bbs_leaf.run_info.extend_props.get("dispatching")
    # side 应为空(bbs 叶被守卫跳过,无 run/group/miss/dispatch_fail 投递)
    assert side == []


@pytest.mark.asyncio
async def test_actual_bbs_override_is_not_auto_dispatched():
    """actual_run_mode=bbs 覆盖 coop_group 时,task_dispatch 仍按 BBS 自驱协议跳过。"""
    svc, tid = _seed_bbs_pending_graph()
    graph = svc._require_graph(tid)
    leaf = next(n for n in graph.tasks if n.node_id == "bbs-leaf-1")
    leaf.run_info.run_mode = "coop_group"
    leaf.run_info.extend_props["actual_run_mode"] = "bbs"

    engine = ExecutionEngine(svc)
    side: list[tuple] = []
    await engine._prepare_into(tid, side)

    assert leaf.status == Status.PENDING
    assert leaf.run_info.run_mode == "coop_group"
    assert leaf.run_info.extend_props["actual_run_mode"] == "bbs"
    assert not leaf.run_info.extend_props.get("dispatching")
    assert side == []
