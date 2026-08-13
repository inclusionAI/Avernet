"""M4b CallbackAdapter + TaskLoopCallback 单测(对齐 tasks.md T4b.x / T4.7)。

覆盖:adapt(TaskCallbackData→TaskNodePatch)映射(PASS/FAIL/output/gaps/fail_detail/loop_task_id 解析)、
TaskLoopCallback.report_result→engine.on_report、start_run 进度信号不驱动。
"""
from __future__ import annotations

import asyncio

from agentclaw.community.core.task.domain.models import (
    AcceptanceVerdict,
    TaskCallbackData,
    TaskNodePatch,
)
from agentclaw.community.core.task.task_runner.callback_adapter import (
    CallbackAdapter,
    TaskLoopCallback,
)


def _data(loop_task_id: str = "t1::c1", *, success: bool = True, data=None, fail_detail=None,
          workflow_type: str = "single_bot") -> TaskCallbackData:
    result: dict = {"success": success}
    if data is not None:
        result["data"] = data
    if fail_detail is not None:
        result["fail_detail"] = fail_detail
    return TaskCallbackData(
        loop_task_id=loop_task_id,
        workflow_type=workflow_type,
        workflow_id=1,
        instance_id=10,
        result=result,
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class RecordingEngine:
    """记录 on_report 入参的编排核 stub(on_report async,匹配协程化签名)。"""

    def __init__(self):
        self.reports: list[TaskNodePatch] = []

    async def on_report(self, patch: TaskNodePatch):
        self.reports.append(patch)
        return patch


# ===== adapt =====
class TestAdapt:
    def test_pass_mapping(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=True, data="行业全貌"))
        assert patch.task_id == "t1"
        assert patch.node_id == "c1"
        assert patch.acceptance_result is not None
        assert patch.acceptance_result.verdict == AcceptanceVerdict.PASS
        assert patch.output_patch == {"data": "行业全貌"}
        assert patch.extend_props_patch is None

    def test_fail_with_detail_mapping(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=False, fail_detail="tech深度不足"))
        assert patch.acceptance_result is not None
        assert patch.acceptance_result.verdict == AcceptanceVerdict.FAIL
        assert patch.acceptance_result.gaps == ["tech深度不足"]
        assert patch.extend_props_patch == {"fail_detail": "tech深度不足"}
        assert patch.output_patch is None  # 无 data

    def test_fail_without_detail_default_gap(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=False))
        assert patch.acceptance_result.verdict == AcceptanceVerdict.FAIL
        assert patch.acceptance_result.gaps == ["unknown_gap"]
        assert patch.extend_props_patch is None

    def test_no_data_no_output_patch(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=True))  # 无 data
        assert patch.output_patch is None

    def test_loop_task_id_parse_coop(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(loop_task_id="t2::N_tech", success=True, data="x", workflow_type="bcn_coop_group"))
        assert patch.task_id == "t2"
        assert patch.node_id == "N_tech"

    def test_loop_task_id_with_double_colon_in_suffix(self):
        # node_id 含冒号也应只切第一个 "::"
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(loop_task_id="t3::grp::sub", success=True, data="x"))
        assert patch.task_id == "t3"
        assert patch.node_id == "grp::sub"


# ===== TaskLoopCallback =====
class TestTaskLoopCallback:
    def test_report_result_routes_to_engine(self):
        engine = RecordingEngine()
        cb = TaskLoopCallback(CallbackAdapter(), engine)
        _run(cb.report_result(_data(loop_task_id="t1::c1", success=True, data="done")))
        assert len(engine.reports) == 1
        p = engine.reports[0]
        assert (p.task_id, p.node_id) == ("t1", "c1")
        assert p.acceptance_result is not None
        assert p.acceptance_result.verdict == AcceptanceVerdict.PASS

    def test_report_result_fail_routes_with_gaps(self):
        engine = RecordingEngine()
        cb = TaskLoopCallback(CallbackAdapter(), engine)
        _run(cb.report_result(_data(loop_task_id="t1::c1", success=False, fail_detail="缺证据")))
        assert engine.reports[0].acceptance_result.gaps == ["缺证据"]

    def test_start_run_is_noop(self):
        engine = RecordingEngine()
        cb = TaskLoopCallback(CallbackAdapter(), engine)
        assert _run(cb.start_run(_data(loop_task_id="t1::c1"))) is None
        assert engine.reports == []  # 进度信号不驱动 on_report


class TestZeroCase:
    def test_no_node_name_literals(self):
        import agentclaw.community.core.task.task_runner.callback_adapter as m
        src = open(m.__file__).read()
        forbidden = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]
        hits = [f for f in forbidden if f in src]
        assert hits == [], f"callback_adapter 出现写死节点名: {hits}"

    def test_no_node_name_literals_runner(self):
        import agentclaw.community.core.task.task_runner.runner as m
        src = open(m.__file__).read()
        forbidden = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]
        hits = [f for f in forbidden if f in src]
        assert hits == [], f"runner 出现写死节点名: {hits}"
