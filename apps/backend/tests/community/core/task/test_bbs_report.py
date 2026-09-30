"""BBS 接力步⑤ on_bbs_report / report_bbs_result 回投单测。

对齐 task-7 brief。BBS scoped 节点回投表示执行完成，统一置为 ``SUCCESS``，不承载验收通过/失败结论，
不删除节点，仅释放 bbs_owner claim。根目标是否满足由框架经 owner 复核(``plan(root)``→``_maybe_finish_graph``)
判定。
"""
import uuid

import pytest

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    Metadata,
    Status,
    TaskGraphPatch,
    TaskInfo,
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


@pytest.fixture
def task_service_with_bbs_node():
    """构造 bbs_mode + 根 PLANNING + claim botA + attach 一个 scoped RUNNING 节点。

    返回 (TaskService, task_id, node_id, botA)。root 经白盒直改 PLANNING(PENDING→PLANNING 不在
    _DIRECT_TRANSITIONS,不可经 update_task_node_info 翻;与 task-3/5 一致),attach_bbs_node 经
    cond_c(存在 PLANNING 节点)接入,scoped 子节点 create+start 合一翻 RUNNING。
    """
    graph = TaskGraphService()
    svc = TaskService(graph)
    tid = "bbs-r7"
    graph.initialize_graph(_ti(tid))
    graph.update_task_graph_info(tid, TaskGraphPatch(extend_props_patch={"bbs_mode": True}))
    # 根 PLANNING 经白盒直改(PENDING→PLANNING 非法态翻,与 task-3/5 一致;
    # query_task_dashboard(node_id=None) 返回存储引用,原地改即落 _graphs[tid])
    stored = graph.query_task_dashboard(tid)
    stored.tasks[0].status = Status.PLANNING
    svc.claim_bbs_task(tid, "botA")
    node = svc.attach_bbs_node(tid, tid, _scoped_spec(), "botA")
    return svc, tid, node.node_id, "botA"


@pytest.mark.asyncio
async def test_report_pass_marks_scoped_done_and_releases_claim(task_service_with_bbs_node):
    """步⑤ PASS:scoped 节点 SUCCESS + claim 释放。根收口由框架经 owner 复核(live 有 planner):``on_bbs_report``
    →``_on_pass_collect``→``plan(root)``→``has_gap=False``→``_maybe_finish_graph``。单测无 owner bot,
    ``plan(root)`` 返 ``no_planning_port``→``gap_no_progress``→根 HUNG,故此处只断 mechanics(scoped SUCCESS +
    claim 释放),不断言图 DONE(收口见 live e2e ``test_bbs_relay_e2e_natual``)。"""
    svc, task_id, node_id, bot = task_service_with_bbs_node
    r = await svc.report_bbs_result(
        task_id, node_id, bot,
        acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
    )
    assert r.success is True
    scoped = next(n for n in svc.get_task_dashboard(task_id).tasks if n.node_id == node_id)
    assert scoped.status == Status.SUCCESS
    root = next(n for n in svc.get_task_dashboard(task_id).tasks if n.node_id == task_id)
    assert root.run_info.extend_props.get("bbs_owner") is None  # claim 已释放


@pytest.mark.asyncio
async def test_report_does_not_delete_node_and_marks_execution_done(task_service_with_bbs_node):
    """兼容传入失败验收结果时，BBS 回投仍只记录执行完成，不删除 scoped 节点。"""
    svc, task_id, node_id, bot = task_service_with_bbs_node
    await svc.report_bbs_result(
        task_id, node_id, bot,
        acceptance_result=AcceptanceResult(AcceptanceVerdict.FAILED, gaps=["partial"]),
        output_patch={"progress": 30},
    )
    tasks = svc.get_task_dashboard(task_id).tasks
    scoped = next(n for n in tasks if n.node_id == node_id)
    assert scoped.status == Status.SUCCESS
    assert scoped.run_info.output["progress"] == 30
    root = next(n for n in tasks if n.node_id == task_id)
    assert root.run_info.extend_props.get("bbs_owner") is None


@pytest.mark.asyncio
async def test_report_rejects_non_owner(task_service_with_bbs_node):
    svc, task_id, node_id, bot = task_service_with_bbs_node
    with pytest.raises(TaskStateError):
        await svc.report_bbs_result(
            task_id, node_id, "botOTHER",
            acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
        )


@pytest.mark.asyncio
async def test_report_clears_owner_even_if_scoped_flip_raises(task_service_with_bbs_node):
    """scoped 翻态抛错(如对已 SUCCESS 的 scoped 节点再报 PASS:SUCCESS→SUCCESS 非法)时,``finally`` 仍须清根
    ``bbs_owner``,避免持卡者死锁(他 bot claim 被 CAS 拒)。owner 校验在 ``try`` 之外,非持有者抛错不清他卡。"""
    svc, task_id, node_id, bot = task_service_with_bbs_node
    # 先正常回投 PASS 一次 → scoped SUCCESS + claim 释放
    await svc.report_bbs_result(
        task_id, node_id, bot,
        acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
    )
    # 重新 claim(模拟同 bot 再报已 DONE 节点)
    svc.claim_bbs_task(task_id, bot)
    # 重复回投仍不删除节点，也不承载验收状态。
    await svc.report_bbs_result(
        task_id, node_id, bot,
        acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
    )
    # 持卡者即使翻态抛错也已被释放,不再死锁
    root_after = next(n for n in svc.get_task_dashboard(task_id).tasks if n.node_id == task_id)
    assert root_after.run_info.extend_props.get("bbs_owner") is None


def test_protocol_has_report_bbs_result_signature():
    """TaskServiceProtocol 声明 report_bbs_result 签名(runtime_checkable Protocol)。"""
    assert hasattr(TaskServiceProtocol, "report_bbs_result")
