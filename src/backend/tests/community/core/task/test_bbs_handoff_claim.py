"""BBS 接力被人接单后 run_mode 必须保持 bbs(回归)_*_"""
import asyncio

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
    TaskNodePatch,
    TaskSpec,
    effective_run_mode,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService

RND = "20260828_f7wfi27d"


def _ti(tid: str) -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=tid, title="claim root", instruction="root"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="botA",
        execution_config={},
    )


def _bbs_leaf(tid: str) -> TaskNode:
    return TaskNode(
        node_id="bbs-leaf",
        task_id=tid,
        status=Status.PENDING,
        task_spec=TaskSpec(
            metadata=Metadata(task_id=tid, title="bbs leaf", instruction="bbs part"),
            context=Context(background="bg"),
            goal=Goal(objective="part", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
        ),
        run_info=RuntimeInfo(run_mode="bbs", assignee=RND),
        node_run_graph=None,  # type: ignore[arg-type]  store 回填
    )


class _LeakyRunner:
    """真机派发经 singlebot_2_group 旁路落库:run_mode=coop_group + extend_props.actual_run_mode
    =single_bot(原派发模式留痕)。这正是 _bbs_handoff_claim 临时切 single_bot 后、被接"成功"路径
    在真机上的副作用;本 stub 等价模拟该副作用并回报派发成功。"""

    def __init__(self, graph):
        self._graph = graph

    async def start_run(self, nodes):
        for n in nodes:
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=n.task_id,
                    node_id=n.node_id,
                    run_mode="coop_group",
                    extend_props_patch={"actual_run_mode": "single_bot"},
                )
            )
        return [True]


@pytest.mark.asyncio
async def test_bbs_handoff_claim_keeps_run_mode_bbs_after_leaked_single_bot(monkeypatch):
    """临时 single_bot 派发旁路泄漏 actual_run_mode=single_bot 后,_bbs_handoff_claim 收尾必须清成 bbs,
    否则 effective_run_mode() 优先读 actual_run_mode 仍判 single_bot,致 dashboard 显"单人 bot"。"""
    svc = TaskGraphService()
    tid = "t-claim"
    svc.initialize_graph(_ti(tid))
    svc.add_task_nodes([_bbs_leaf(tid)], parent_node_id=tid)

    engine = ExecutionEngine(svc)
    engine._runner = _LeakyRunner(svc)
    monkeypatch.setattr(engine, "_bbs_handoff_delay", lambda *a, **k: 0.0)

    async def _noop_auto_report(*a, **k):
        return None

    monkeypatch.setattr(engine, "_static_bbs_handoff_auto_report", _noop_auto_report)

    await engine._bbs_handoff_claim(tid, "bbs-leaf", RND, [{"id": "merchant-coupon-duplicate-claim"}])
    # 让被接收尾挂的后台兜底上报协程落地,避免 loop 关闭时"task pending"警告
    await asyncio.gather(*engine._bg_tasks, return_exceptions=True)

    leaf = next(n for n in svc.query_task_dashboard(tid).tasks if n.node_id == "bbs-leaf")
    assert leaf.status == Status.RUNNING
    assert leaf.run_info.run_mode == "bbs"
    assert leaf.run_info.extend_props.get("actual_run_mode") == "bbs"
    assert effective_run_mode(leaf) == "bbs"
    assert leaf.run_info.extend_props.get("bbs_status") == "claimed_by_rnd"
    assert leaf.run_info.assignee == RND
