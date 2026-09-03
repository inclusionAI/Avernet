"""M4b CallbackAdapter + TaskLoopCallback 单测(对齐 tasks.md T4b.x / T4.7)。

覆盖:adapt(TaskCallbackData→TaskNodePatch)映射(PASS/FAIL/output/gaps/fail_detail/loop_task_id 解析)、
TaskLoopCallback.report_result→engine.on_report、start_run 进度信号不驱动。
"""
from __future__ import annotations

import asyncio

from agentclaw.community.core.task.domain.models import (
    AcceptanceVerdict,
    Status,
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
    return TaskCallbackData(data={
        "loop_task_id": loop_task_id,
        "workflow_type": workflow_type,
        "workflow_id": 1,
        "instance_id": 10,
        "result": result,
    })


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class RecordingEngine:
    """记录 on_report / on_start 入参的编排核 stub(async,匹配协程化签名)。"""

    def __init__(self):
        self.reports: list[TaskNodePatch] = []
        self.starts: list[TaskNodePatch] = []

    async def on_report(self, patch: TaskNodePatch):
        self.reports.append(patch)
        return patch

    async def on_start(self, patch: TaskNodePatch):
        self.starts.append(patch)
        return patch


# ===== adapt =====
class TestAdapt:
    def test_pass_mapping(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=True, data="行业全貌"))
        assert patch.task_id == "t1"
        assert patch.node_id == "c1"
        assert patch.acceptance_result is not None
        assert patch.acceptance_result.verdict == AcceptanceVerdict.DONE
        assert patch.output_patch == {"output": "行业全貌"}
        assert patch.extend_props_patch is None

    def test_pass_flattens_result_wrapper_to_bare_string(self):
        """pull 拉取的原始 response ``data`` 可能为 ``{"result": <str>}`` 二次包裹
        (bot 把结论挂在 result key);统一展平为裸字符串,与 push(callback/report output 裸字符串)
        一致,消除 dashboard ``{output: {result: ...}}`` 二次 json 嵌套。"""
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=True, data={"result": "技术栈概览文档已完整产出..."}))
        assert patch.output_patch == {"output": "技术栈概览文档已完整产出..."}  # 裸字符串,不带 result

    def test_pass_keeps_non_result_dict_unchanged(self):
        """非 ``result`` 包裹的多键 dict(bcs/notify 检查点等)原样保留,不被误展平。"""
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=True, data={"r": 1}))
        assert patch.output_patch == {"output": {"r": 1}}

    def test_fail_with_detail_mapping(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=False, fail_detail="tech深度不足"))
        assert patch.acceptance_result is not None
        assert patch.acceptance_result.verdict == AcceptanceVerdict.FAILED
        assert patch.acceptance_result.gaps == ["tech深度不足"]
        assert patch.extend_props_patch == {"fail_detail": "tech深度不足"}
        assert patch.output_patch is None  # 无 data

    def test_fail_without_detail_default_gap(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=False))
        assert patch.acceptance_result is None
        assert patch.exec_error == "terminal_result_invalid: failed result requires gaps"

    def test_success_string_is_terminal_contract_error(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success="false"))
        assert patch.acceptance_result is None
        assert patch.exec_error == "terminal_result_invalid: success must be bool"
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

    def test_adapt_folds_ext_info_into_extend_props(self):
        adapter = CallbackAdapter()
        d = _data(success=True, data="ok")
        d.data["result"]["_ext_info"] = {"k": "v"}
        patch = adapter.adapt(d)
        assert patch.extend_props_patch == {"k": "v"}

    def test_adapt_merges_ext_info_and_fail_detail(self):
        adapter = CallbackAdapter()
        d = _data(success=False, fail_detail="gap1")
        d.data["result"]["_ext_info"] = {"k": "v"}
        patch = adapter.adapt(d)
        assert patch.extend_props_patch == {"k": "v", "fail_detail": "gap1"}


# ===== adapt_start =====
class TestAdaptStart:
    def test_adapt_start_builds_running_patch_with_ext_info(self):
        from agentclaw.community.core.task.domain.models import Status
        adapter = CallbackAdapter()
        d = _data(loop_task_id="t1::c1")
        d.data["result"]["_ext_info"] = {"k": "v"}
        patch = adapter.adapt_start(d)
        assert (patch.task_id, patch.node_id) == ("t1", "c1")
        assert patch.status == Status.RUNNING
        assert patch.acceptance_result is None
        assert patch.extend_props_patch == {"k": "v"}

    def test_adapt_start_without_ext_info_has_no_extend_props(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt_start(_data(loop_task_id="t1::c1"))
        assert patch.extend_props_patch is None


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
        assert p.acceptance_result.verdict == AcceptanceVerdict.DONE

    def test_report_result_fail_routes_with_gaps(self):
        engine = RecordingEngine()
        cb = TaskLoopCallback(CallbackAdapter(), engine)
        _run(cb.report_result(_data(loop_task_id="t1::c1", success=False, fail_detail="缺证据")))
        assert engine.reports[0].acceptance_result.gaps == ["缺证据"]

    def test_start_run_routes_to_on_start(self):
        from agentclaw.community.core.task.domain.models import Status
        engine = RecordingEngine()
        cb = TaskLoopCallback(CallbackAdapter(), engine)
        _run(cb.start_run(_data(loop_task_id="t1::c1")))
        assert engine.reports == []          # start 不走 on_report
        assert len(engine.starts) == 1
        p = engine.starts[0]
        assert (p.task_id, p.node_id) == ("t1", "c1")
        assert p.status == Status.RUNNING
        assert p.acceptance_result is None   # start 不带 acceptance


class TestPersist:
    """回投落库:data 为 dict → 解析字段 upsert task_callback;非 dict / 无 repo → 不落。"""

    class _FakeRepo:
        def __init__(self):
            self.calls = []

        def upsert(self, rec):
            self.calls.append(rec)
            return rec

    def test_persist_dict_upserts_with_parsed_fields(self):
        repo = self._FakeRepo()
        cb = TaskLoopCallback(CallbackAdapter(), RecordingEngine(), callback_repo=repo)
        _run(cb.report_result(_data(loop_task_id="t1::c1", success=True, data="done")))
        assert len(repo.calls) == 1
        rec = repo.calls[0]
        assert rec.run_id == "t1" and rec.node_id == "c1"
        assert rec.result_success is True
        assert rec.exec_error is None
        assert rec.event_id is not None
        assert rec.event_id.startswith("t1:c1:result:")
        # 无 workflow_source/instance_in data → NOT NULL 列退 ""(空保持空)
        assert rec.invoker == ""
        assert rec.main_session_id == ""
        assert rec.orig_callback_data  # payload 的 JSON

    def test_persist_maps_source_and_instance(self):
        repo = self._FakeRepo()
        cb = TaskLoopCallback(CallbackAdapter(), RecordingEngine(), callback_repo=repo)
        _run(cb.report_result(TaskCallbackData(data={
            "loop_task_id": "t::n", "workflow_source": "bcn",
            "workflow_instance_id": "i9", "result": {"success": False, "exec_error": "boom"},
        })))
        rec = repo.calls[0]
        assert rec.invoker == "bcn"
        assert rec.main_session_id == "i9"
        assert rec.result_success is False
        assert rec.exec_error == "boom"

    def test_persist_skipped_when_data_not_dict(self):
        repo = self._FakeRepo()
        cb = TaskLoopCallback(CallbackAdapter(), RecordingEngine(), callback_repo=repo)
        _run(cb.report_result(TaskCallbackData(data="not-a-dict")))
        assert repo.calls == []  # 非 dict 不落库

    def test_persist_skipped_when_no_repo(self):
        cb = TaskLoopCallback(CallbackAdapter(), RecordingEngine())  # callback_repo=None
        _run(cb.report_result(_data(loop_task_id="t1::c1", success=True)))  # 不抛、推进引擎

    def test_persist_claw_mind_maps_to_table_fields(self):
        # ClawMind HttpCallbackPayload → task_callback 记录(字段对齐 task_callback 列)+ 落库
        from agentclaw.community.adapters.http.task.translator import translate_claw_mind
        import json as _json
        raw = {
            "workflow_id": "wf-1", "flow_id": "fl-1", "status": "node_succeeded",
            "ext_info": {"flow_runs": {"status": "succeeded", "origin_session_id": "S-9"},
                         "node_executions": [{"node_id": "N1", "status": "succeeded",
                                               "output_json": {"answer": 42}, "error_text": None}]},
        }
        repo = self._FakeRepo()
        engine = RecordingEngine()
        cb = TaskLoopCallback(CallbackAdapter(), engine, callback_repo=repo)
        _run(cb.ingest(translate_claw_mind(raw, "result").data))   # 只落库,不推进引擎
        assert len(repo.calls) == 1
        assert engine.reports == [] and engine.starts == []         # ingest 不推进编排核
        rec = repo.calls[0]
        assert rec.invoker == "claw_mind"
        assert rec.run_id == "fl-1" and rec.node_id == ""          # loop_task_id = flow_id(run 实例,对齐 BCN)
        assert rec.main_session_id == "S-9"                        # origin_session_id
        assert rec.status == "DONE"                                 # flow_runs.status=succeeded → task Status.DONE
        assert rec.result_success is True
        assert rec.exec_error is None
        assert rec.result == {"success": True, "data": {"answer": 42}}
        # execution_graph 不在 translator 产物(由 CallbackDataEnricher.enrich_claw_mind 构建后落库)
        assert rec.execution_graph is None
        assert rec.extend_props is None                            # claw_mind 无额外扩展
        assert _json.loads(rec.orig_callback_data) == raw          # 原始 body

    def test_persist_bcn_maps_to_table_fields(self):
        # BCN CloudEvent → task_callback 记录(字段对齐)+ 落库
        from agentclaw.community.adapters.http.task.translator import translate_bcn
        import json as _json
        raw = {
            "spec_version": "1.0", "event_id": "evt-1",
            "event_type": "state_machine.node.completed", "source": "bcs",
            "scope": {"group_id": "g1", "session_id": "s-1", "run_id": "run-1"},
            "stream": {"key": "state-machine-run:run-1", "sequence": 6},
            "actor": {"type": "bot", "id": "b1"},
            "data": {"run_id": "run-1", "node_id": "N1", "attempt": 1,
                     "outcome": "success", "output": {"answer": 7}},
        }
        repo = self._FakeRepo()
        engine = RecordingEngine()
        cb = TaskLoopCallback(CallbackAdapter(), engine, callback_repo=repo)
        _run(cb.ingest(translate_bcn(raw).data))                    # 只落库,不推进引擎
        assert len(repo.calls) == 1
        assert engine.reports == [] and engine.starts == []         # ingest 不推进编排核
        rec = repo.calls[0]
        assert rec.invoker == "bcn"
        assert rec.run_id == "run-1" and rec.node_id == ""        # req4:node_id 恒空
        assert rec.main_session_id == "s-1"                       # scope.session_id
        assert rec.status == "RUNNING"                            # req2:node.completed → Status.RUNNING
        assert rec.result_success is True
        assert rec.result == {"success": True, "data": {"answer": 7}}
        # execution_graph 不在 translator 产物(由 CallbackDataEnricher.enrich_bcn 取 run 明细后构建)
        assert rec.execution_graph is None
        assert rec.extend_props is None
        assert _json.loads(rec.orig_callback_data) == raw         # 原始 event

    def test_persist_does_not_block_engine_on_repo_failure(self):
        class _BrokenRepo:
            def upsert(self, rec):
                raise RuntimeError("db down")
        cb = TaskLoopCallback(CallbackAdapter(), RecordingEngine(), callback_repo=_BrokenRepo())
        engine = RecordingEngine()
        cb2 = TaskLoopCallback(CallbackAdapter(), engine, callback_repo=_BrokenRepo())
        _run(cb2.report_result(_data(loop_task_id="t1::c1", success=True, data="done")))
        # 落库失败不阻断推进
        assert len(engine.reports) == 1
        assert (engine.reports[0].task_id, engine.reports[0].node_id) == ("t1", "c1")


class TestZeroCase:
    def test_no_node_name_literals(self):
        import agentclaw.community.core.task.task_runner.callback_adapter as m
        src = open(m.__file__).read()
        forbidden = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]
        hits = [f for f in forbidden if f in src]
        assert hits == [], f"callback_adapter 出现写死节点名: {hits}"

    def test_no_node_name_literals_runner(self):
        import agentclaw.community.core.task.task_runner.task_runner as m
        src = open(m.__file__).read()
        forbidden = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]
        hits = [f for f in forbidden if f in src]
        assert hits == [], f"runner 出现写死节点名: {hits}"

    def test_actual(self):
        adapter = CallbackAdapter()
        data = {'loop_task_id': '46fa696d-1714-4294-bc36-a46c709637e2', 'node_id': 'N_dual_view', 'workflow_type': 'task_loop', 'workflow_id': 0, 'instance_id': 0, 'workflow_source': 'task_loop', 'workflow_instance_id': '', 'event_id': '', 'status': 'DONE', '_raw_callback_body': {'task_id': '46fa696d-1714-4294-bc36-a46c709637e2', 'node_id': 'N_dual_view', 'status': 'DONE', 'output': '完整分析内容...', 'acceptance_result': {'verdict': 'DONE', 'acceptances_metric': [{'ac_1': 'exec_ok'}], 'gaps': []}, 'extend_props': {}}, 'result': '完整分析内容...'}
        task_callback_data = TaskCallbackData(data=data)
        res = adapter.adapt(task_callback_data)

        print("===" + str(res))

    def test_common_task_output_unified_to_output_key_no_double_output(self):
        """push(skill HTTP /callback/report 上报)与 pull(poller)两条链路统一按上报协议把产出
        挂在 ``output`` key;两条链路写入 run_info.output 的字段一致,DTO 层再展平成标量。
        不得让 push 存成 ``{"data": ...}`` 与 pull 不一致。"""
        adapter = CallbackAdapter()
        patch = adapter.adapt(TaskCallbackData(data={
            "loop_task_id": "t9::N1", "node_id": "N1", "workflow_type": "task_loop",
            "status": "DONE",
            "_raw_callback_body": {
                "task_id": "t9", "node_id": "N1", "status": "DONE",
                "output": "完整分析内容...",
                "acceptance_result": {"verdict": "DONE", "acceptances_metric": [], "gaps": []},
                "extend_props": {},
            },
            "result": "完整分析内容...",
        }))
        assert patch.status == Status.DONE
        assert patch.output_patch == {"output": "完整分析内容..."}  # 统一 output key,对齐 callback/report 协议
        assert patch.acceptance_result is not None
        assert patch.acceptance_result.verdict == AcceptanceVerdict.DONE



def test_ingest_parse_error_without_repository_is_noop():
    cb = TaskLoopCallback(CallbackAdapter(), RecordingEngine())
    _run(cb.ingest_parse_error({"flow_id": "flow-1"}, "bad embedded json"))
