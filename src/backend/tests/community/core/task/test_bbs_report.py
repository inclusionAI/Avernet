"""BBS 接力步⑤ on_bbs_report / report_bbs_result 回投(collector-free)单测。

对齐 task-7 brief(TDD RED→GREEN)。scoped 节点终态回投(PASS→DONE / FAIL+gaps→FAILED +
output_patch fold)+ root_verified 根 PLANNING→DONE + 图 DONE + 释放 bbs_owner claim。
collector-free:不跑 _on_pass_collect/_on_fail_collect/_drain(避免框架经 owner-bot 重规划
抢占 bot 接力,对齐 spec §10.4)。
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
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


def _ti(tid):
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=tid, title="t", instruction="i"),
            context=Context(background="", extend_props={}),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
        ),
        source_channel_type="bot",
        source_channel_id="b1",
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
async def test_report_pass_finishes_graph_when_root_verified(task_service_with_bbs_node):
    svc, task_id, node_id, bot = task_service_with_bbs_node
    r = await svc.report_bbs_result(
        task_id, node_id, bot,
        acceptance_result=AcceptanceResult(AcceptanceVerdict.PASS), root_verified=True,
    )
    assert r.success is True
    assert svc.get_task_dashboard(task_id).status == Status.DONE
    # claim 已释放
    root = next(n for n in svc.get_task_dashboard(task_id).tasks if n.node_id == task_id)
    assert root.run_info.extend_props.get("bbs_owner") is None


@pytest.mark.asyncio
async def test_report_fail_partial_releases_claim(task_service_with_bbs_node):
    svc, task_id, node_id, bot = task_service_with_bbs_node
    await svc.report_bbs_result(
        task_id, node_id, bot,
        acceptance_result=AcceptanceResult(AcceptanceVerdict.FAIL, gaps=["partial"]),
        output_patch={"progress": 30},
    )
    root = next(n for n in svc.get_task_dashboard(task_id).tasks if n.node_id == task_id)
    assert root.run_info.extend_props.get("bbs_owner") is None  # 释放
    scoped = next(n for n in svc.get_task_dashboard(task_id).tasks if n.node_id == node_id)
    assert scoped.status == Status.FAILED
    assert scoped.run_info.output.get("progress") == 30  # checkpoint 保留


@pytest.mark.asyncio
async def test_report_rejects_non_owner(task_service_with_bbs_node):
    svc, task_id, node_id, bot = task_service_with_bbs_node
    with pytest.raises(TaskStateError):
        await svc.report_bbs_result(
            task_id, node_id, "botOTHER",
            acceptance_result=AcceptanceResult(AcceptanceVerdict.PASS),
        )


@pytest.mark.asyncio
async def test_report_clears_owner_on_mid_path_raise(task_service_with_bbs_node):
    """root_verified=True 在根 HUNG 时翻态 HUNG→DONE 非法(_DIRECT_TRANSITIONS 无 HUNG 出边)→抛
    TaskStateError;try/finally 须仍清根 bbs_owner,避免持卡者死锁(他 bot claim 被 CAS 拒、
    持卡者重报已 DONE 节点再翻 DONE 亦非法)。owner 校验在 try 之外,故非持有者抛错不清他卡。"""
    svc, task_id, node_id, bot = task_service_with_bbs_node
    # 白盒置根 HUNG(模拟 bbs_relay_exhausted 等已 HUNG 的根);get_task_dashboard(node_id=None) 返回存储引用
    stored = svc.get_task_dashboard(task_id)
    root = next(n for n in stored.tasks if n.node_id == task_id)
    root.status = Status.HUNG
    with pytest.raises(TaskStateError):
        await svc.report_bbs_result(
            task_id, node_id, bot,
            acceptance_result=AcceptanceResult(AcceptanceVerdict.PASS), root_verified=True,
        )
    # 持卡者即使翻态抛错也已被释放,不再死锁
    root_after = next(n for n in svc.get_task_dashboard(task_id).tasks if n.node_id == task_id)
    assert root_after.run_info.extend_props.get("bbs_owner") is None


def test_protocol_has_report_bbs_result_signature():
    """TaskServiceProtocol 声明 report_bbs_result 签名(runtime_checkable Protocol)。"""
    assert hasattr(TaskServiceProtocol, "report_bbs_result")
