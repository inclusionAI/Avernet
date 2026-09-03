import pytest

from agentclaw.community.adapters.http.task.schemas import (
    TaskCallbackRequest, TaskNodeCallbackRequest,
)
from agentclaw.community.adapters.http.task.translator import (
    is_bcn_event_payload, is_claw_mind_payload, translate, translate_bcn, translate_claw_mind,
)
from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.task.task_runner.callback_correlation import (
    InMemoryCallbackCorrelationRegistry,
)
from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (
    LocalBcsTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.callback_data_enricher import (
    CallbackDataEnricher,
)


def _claw_mind_graph(raw, disposition="result"):
    """translate_claw_mind + enrich_claw_mind → execution_graph(execution_graph 构建已移至 CallbackDataEnricher)。"""
    _tc = translate_claw_mind(raw, disposition)
    CallbackDataEnricher(LocalBcsTokenProvider(base_url="http://bcs")).enrich_claw_mind(_tc.data, raw)
    return _tc.data.data.get("execution_graph")


def _reg_with(source="bcn", instance_id_str="i1", **kw):
    reg = InMemoryCallbackCorrelationRegistry()
    reg.register(source=source, workflow_id=7, instance_id=77,
                 task_id="t1", node_id="root1", loop_task_id="t1::root1",
                 workflow_id_str="w7", instance_id_str=instance_id_str, **kw)
    return reg


def _base(**kw):
    d = dict(task_id="t1", workflow_source="bcn", workflow_id="w7",
             workflow_instance_id="i1", status="COMPLETED", is_success=True)
    d.update(kw)
    return d


class TestLoopTaskIdResolution:
    def test_node_level_builds_loop_task_id_from_node_id(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", output={"r": 1}))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.data["loop_task_id"] == "t1::c1"

    def test_task_level_uses_echo_loop_task_id(self):
        req = TaskCallbackRequest(**_base(loop_task_id="t1::root1"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.data["loop_task_id"] == "t1::root1"

    def test_task_level_no_echo_resolves_via_registry(self):
        reg = _reg_with()
        req = TaskCallbackRequest(**_base())  # 无 loop_task_id 回声
        tc = translate(req, "result", reg)
        assert tc.data.data["loop_task_id"] == "t1::root1"

    def test_task_level_no_echo_no_registry_raises(self):
        req = TaskCallbackRequest(**_base())
        with pytest.raises(NotFound):
            translate(req, "result", InMemoryCallbackCorrelationRegistry())


class TestFieldFolding:
    def test_success_folds_output_to_result_data(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", output={"r": 1}, is_success=True))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.data["result"]["success"] is True
        assert tc.data.data["result"]["data"] == {"r": 1}
        assert "fail_detail" not in tc.data.data["result"]

    def test_fail_folds_failed_info_to_fail_detail(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", is_success=False, failed_info="boom"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.data["result"]["success"] is False
        assert tc.data.data["result"]["fail_detail"] == "boom"

    def test_ext_info_and_goal_folded_into_ext(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", ext_info={"k": "v"}, goal="G"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        ext = tc.data.data["result"]["_ext_info"]
        assert ext["k"] == "v"
        assert ext["_callback_goal"] == "G"

    def test_workflow_source_maps_to_workflow_type(self):
        req = TaskNodeCallbackRequest(**_base(workflow_source="claw_mind", node_id="c1"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.data["workflow_type"] == "single_bot"

    def test_registry_provides_ssot_int_ids(self):
        reg = _reg_with()
        req = TaskCallbackRequest(**_base(loop_task_id="t1::root1"))
        tc = translate(req, "result", reg)
        assert tc.data.data["workflow_id"] == 7
        assert tc.data.data["instance_id"] == 77

    def test_unregistered_node_level_falls_back_zero_int_and_stashes_str(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.data["workflow_id"] == 0
        assert tc.data.data["result"]["_ext_info"]["_workflow_id_str"] == "w7"
        assert tc.data.data["result"]["_ext_info"]["_instance_id_str"] == "i1"


class TestDisposition:
    def test_result_disposition_passes_through(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1"))
        assert translate(req, "result", InMemoryCallbackCorrelationRegistry()).disposition == "result"

    def test_start_disposition_passes_through(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", status="RUNNING"))
        assert translate(req, "start", InMemoryCallbackCorrelationRegistry()).disposition == "start"


class TestClawMind:
    """ClawMind HttpCallbackPayload 解析(语雀《ClawMind回调服务》§八)。"""

    _BODY = {
        "workflow_id": "risk-review-pipeline",
        "flow_id": "flow-abc-123",
        "status": "node_succeeded",  # 顶层仅粗粒度事件类型,取值不统一
        "ext_info": {
            "flow_runs": {"id": "fr1", "flow_id": "flow-abc-123",
                          "status": "succeeded", "origin_session_id": "S-9"},
            "node_executions": [
                {"node_id": "N1", "status": "succeeded",
                 "output_json": {"answer": 42}, "error_text": None},
            ],
        },
    }

    def test_is_claw_mind_payload_detects_four_fields(self):
        assert is_claw_mind_payload(self._BODY) is True

    def test_is_claw_mind_payload_rejects_missing_field(self):
        b = dict(self._BODY)
        del b["ext_info"]
        assert is_claw_mind_payload(b) is False

    def test_is_claw_mind_payload_rejects_non_dict(self):
        assert is_claw_mind_payload(None) is False
        assert is_claw_mind_payload("not-a-dict") is False

    def test_translate_claw_mind_maps_fields(self):
        tc = translate_claw_mind(self._BODY, "result")
        CallbackDataEnricher(LocalBcsTokenProvider(base_url="http://bcs")).enrich_claw_mind(tc.data, self._BODY)
        assert tc.disposition == "result"
        d = tc.data.data
        assert d["loop_task_id"] == "flow-abc-123"  # loop_task_id = flow_id(run 实例,对齐 BCN);node_id 空
        assert d["workflow_source"] == "claw_mind"
        assert d["workflow_instance_id"] == "S-9"        # session_id = origin_session_id
        assert d["status"] == "DONE"                        # flow_runs.status=succeeded → task Status.DONE(落 task_callback.status)
        assert d["result"]["success"] is True
        assert d["result"]["data"] == {"answer": 42}
        # execution_graph 转结构化 TaskExecutionGraph(graph_to_dict 形状),非原始 ext 透传
        eg = d["execution_graph"]
        assert eg["run_id"] == 0                           # flow_runs.id="fr1" 非整 → 0
        assert eg["status"] == "DONE"                       # succeeded → DONE
        assert eg["output"] == {}                           # 无 result_json
        assert eg["extend_props"]["flow_id"] == "flow-abc-123"
        assert len(eg["tasks"]) == 1
        assert eg["tasks"][0]["node_id"] == "N1"
        assert eg["tasks"][0]["status"] == "DONE"
        assert eg["tasks"][0]["task_spec"]["metadata"]["title"] == "N1"  # 无 node_title → 退 node_id
        assert eg["tasks"][0]["run_info"]["output"] == {"answer": 42}
        assert eg["relations"] == []                        # N1 无 nodeOutputKeys
        assert d["_raw_callback_body"] == self._BODY            # 原始 body → orig_callback_data
        assert "_ext_info" not in d["result"]                    # 不再重复进 result

    def test_translate_claw_mind_status_from_node_and_failed(self):
        # flow_runs 无 status → 底层退到 node_executions[0].status;failed→success False + exec_error
        body = {
            "workflow_id": "w", "flow_id": "f", "status": "node_failed",
            "ext_info": {
                "flow_runs": {"origin_session_id": "S-1"},
                "node_executions": [{"node_id": "N1", "status": "failed",
                                      "output_json": None, "error_text": "boom"}],
            },
        }
        d = translate_claw_mind(body, "result").data.data
        assert d["loop_task_id"] == "f"  # loop_task_id = flow_id(run 实例,对齐 BCN)
        assert d["status"] == "FAILED"                      # node status=failed → task Status.FAILED
        assert d["workflow_instance_id"] == "S-1"
        assert d["result"]["success"] is False
        assert d["result"]["exec_error"] == "boom"

    def test_translate_claw_mind_top_status_fallback_unknown_success(self):
        # 无 flow_runs.status / node_executions → status 退顶层;未知 status 不设 success
        body = {"workflow_id": "w", "flow_id": "f", "status": "started", "ext_info": {}}
        d = translate_claw_mind(body, "start").data.data
        assert d["status"] == "RUNNING"                     # 顶层退到 status=started → task Status.RUNNING
        assert "success" not in d["result"]

    def test_translate_claw_mind_session_id_empty_when_absent(self):
        body = {"workflow_id": "w", "flow_id": "f", "status": "succeeded",
                "ext_info": {"flow_runs": {"status": "succeeded"}}}
        assert translate_claw_mind(body, "result").data.data["workflow_instance_id"] == ""

    def test_session_id_prefers_origin_session_key(self):
        # main_session_id 应来自 flow_runs.origin_session_key(完整 session key),优先于 origin_session_id
        body = {"workflow_id": "w", "flow_id": "f", "status": "succeeded",
                "ext_info": {"flow_runs": {"status": "succeeded",
                             "origin_session_id": "S-9",
                             "origin_session_key": "agent:main:session:S-9:user:35983"}}}
        assert translate_claw_mind(body, "result").data.data["workflow_instance_id"] \
            == "agent:main:session:S-9:user:35983"

    def test_execution_graph_final_output_in_output_and_extend_props(self):
        # 最终输出(result_json)在 execution_graph 顶层 output 与 extend_props.output 两处都以 output 可取
        body = {"workflow_id": "w", "flow_id": "f", "status": "succeeded",
                "ext_info": {"flow_runs": {"status": "succeeded",
                             "result_json": '{"phase":"P3","nodeSummary":"succeeded=3"}',
                             "origin_session_key": "agent:main:session:S-x:user:1"},
                             "node_executions": []}}
        eg = _claw_mind_graph(body, "result")
        assert eg["output"] == {"phase": "P3", "nodeSummary": "succeeded=3"}
        assert eg["extend_props"]["output"] == {"phase": "P3", "nodeSummary": "succeeded=3"}

    def test_malformed_embedded_json_raises_to_skip_persist(self):
        # 内嵌 JSON 非法 → 构图抛错(供 router 捕获后打日志、不落库,避免污染已有记录)
        body = {"workflow_id": "w", "flow_id": "f", "status": "succeeded",
                "ext_info": {"flow_runs": {"status": "succeeded",
                             "result_json": "not-a-valid-json{"},
                             "node_executions": []}}
        with pytest.raises(ValueError):
            _claw_mind_graph(body, "result")

    # 真实 ClawMind 回投 shape:flow_runs/node_executions 在 ext_info 下,*_json 为 JSON 字符串,
    # 节点 DAG 由各节点 input_json.params.nodeOutputKeys 表达(report 多父)。
    _REAL_BODY = {
        "workflow_id": "tech-research-pipeline-simple-pre-2",
        "flow_id": "c5d77c99-f299-42d8-98ce-ead330f0dfd6",
        "status": "node_succeeded",
        "ext_info": {
            "flow_runs": {
                "id": 36018,
                "flow_id": "c5d77c99-f299-42d8-98ce-ead330f0dfd6",
                "workflow_id": "tech-research-pipeline-simple-pre-2",
                "workflow_title": "技术调研工作流（精简离线版）",
                "status": "succeeded",
                "params_json": '{"topic":"大模型"}',
                "input_json": '{"message":null,"params":{"topic":"大模型"},"digest":"x","fileCount":0}',
                "result_json": '{"status":"succeeded","flowId":"c5d77","workflowId":"tech-research-pipeline-simple-pre-2","phase":"P3","durationMs":237223,"nodeSummary":"succeeded=3","lastNodeOutput":{"nodeId":"report"},"outputs":{"report_path":"runs/x/report.md","conclusions":["结论1"]}}',
                "node_count": 3,
                "succeeded_count": 4,   # 脏数据(实为 3 节点),不应原样进快照
                "failed_count": 0,
                "total_duration_ms": 237223,
                "total_token_usage": None,
                "triggered_by": "facade:tech-research-pipeline-simple-pre-2",
                "current_phase": None,
                "started_at": 1787719147,
                "completed_at": 1787719384,
                "origin_session_id": "1ccf28e2-1155-4e36-9a97-5c9d2db2c3c4",
                "credentials_json": '{"TOKEN":"DEVICE-xxxx"}',   # 鉴权密钥,不进快照
                "identity_key": "f526...c274",                     # 输入摘要,不进快照
                "plugin_version": "0.9.0",                         # 不进快照
                "origin_session_key": "agent:main:session:1ccf:user:35983",
                "origin_bot_id": "20260824_nwlj25w6:35983",
                "user_id": "35983",
            },
            "node_executions": [
                {"id": 464121, "node_id": "report", "executor_type": "embedded-agent",
                 "status": "succeeded", "attempt": 1,
                 "input_json": '{"params":{"topic":"大模型","sessionKey":"s","ownerId":"35983"},"nodeOutputKeys":["scope-decompose","analysis"]}',
                 "error_text": None, "duration_ms": 118282,
                 "token_usage_json": '{"input":54521,"output":4024,"cacheRead":20608,"totalTokens":27121,"toolCalls":2}',
                 "node_title": "调研报告",
                 "system_context_json": '{"outputContractValidated":true,"outputContractIssues":0}',
                 "embedded_session_key": "agent:main:embedded:report:c5d77",
                 "session_key": "agent:main:session:1ccf:user:35983",
                 "session_id": "1ccf28e2-1155-4e36-9a97-5c9d2db2c3c4",
                 "started_at": 1787719266, "completed_at": 1787719384},
                {"id": 464086, "node_id": "analysis", "executor_type": "embedded-agent",
                 "status": "succeeded", "attempt": 1,
                 "input_json": '{"params":{"topic":"大模型"},"nodeOutputKeys":["scope-decompose"]}',
                 "error_text": None, "duration_ms": 82179,
                 "token_usage_json": '{"input":1761,"output":2728,"cacheRead":16064,"totalTokens":20553}',
                 "node_title": "综合分析",
                 "system_context_json": '{"outputContractValidated":true,"outputContractIssues":0}',
                 "started_at": 1787719184, "completed_at": 1787719266},
                {"id": 464047, "node_id": "scope-decompose", "executor_type": "embedded-agent",
                 "status": "succeeded", "attempt": 1,
                 "input_json": '{"params":{"topic":"大模型"},"nodeOutputKeys":[]}',
                 "error_text": None, "duration_ms": 33361,
                 "token_usage_json": '{"input":6255,"output":1117,"cacheRead":10304,"totalTokens":17676}',
                 "node_title": "调研拆题",
                 "system_context_json": '{"outputContractValidated":true,"outputContractIssues":0}',
                 "started_at": 1787719150, "completed_at": 1787719183},
            ],
        },
    }

    def test_execution_graph_is_structured_task_graph_real_payload(self):
        g = _claw_mind_graph(self._REAL_BODY, "result")
        assert g is not None
        # 图级
        assert g["run_id"] == 36018
        assert g["task_id"] == ""
        assert g["loop_round"] == 0
        assert g["status"] == "DONE"
        assert g["output"]["phase"] == "P3"
        assert g["output"]["nodeSummary"] == "succeeded=3"
        assert g["extend_props"]["workflow_id"] == "tech-research-pipeline-simple-pre-2"
        assert g["extend_props"]["workflow_title"] == "技术调研工作流（精简离线版）"
        assert g["extend_props"]["flow_id"] == "c5d77c99-f299-42d8-98ce-ead330f0dfd6"
        assert g["extend_props"]["params"] == {"topic": "大模型"}
        assert g["extend_props"]["total_duration_ms"] == 237223
        assert g["extend_props"]["triggered_by"] == "facade:tech-research-pipeline-simple-pre-2"
        assert g["extend_props"]["origin_session_id"] == "1ccf28e2-1155-4e36-9a97-5c9d2db2c3c4"
        # 密钥/摘要/版本/脏计数不进快照
        for forbidden in ("credentials_json", "identity_key", "plugin_version",
                          "succeeded_count", "failed_count", "node_count"):
            assert forbidden not in g["extend_props"], f"{forbidden} 不应进 execution_graph"
        # 节点
        nodes = {n["node_id"]: n for n in g["tasks"]}
        assert set(nodes) == {"report", "analysis", "scope-decompose"}
        report = nodes["report"]
        assert report["task_id"] == ""
        assert report["status"] == "DONE"
        assert report["task_spec"]["metadata"]["task_id"] == "report"
        assert report["task_spec"]["metadata"]["title"] == "调研报告"
        assert report["task_spec"]["metadata"]["instruction"] == ""
        assert report["task_spec"]["goal"]["acceptances"] == []
        assert report["run_info"]["start_time"] == 1787719266000   # 秒 → 毫秒
        assert report["run_info"]["end_time"] == 1787719384000
        assert report["run_info"]["run_mode"] is None
        assert report["run_info"]["output"] == {}                    # 真实 payload 节点无 output_json
        rep = report["run_info"]["extend_props"]
        assert rep["executor_type"] == "embedded-agent"
        assert rep["attempt"] == 1
        assert rep["token_usage"]["input"] == 54521
        assert rep["token_usage"]["totalTokens"] == 27121
        assert rep["duration_ms"] == 118282
        assert rep["started_at"] == 1787719266                       # 原始秒保留
        assert rep["input"]["nodeOutputKeys"] == ["scope-decompose", "analysis"]   # input_json 顶层
        assert rep["input"]["params"]["topic"] == "大模型"
        assert rep["system_context"] == {"outputContractValidated": True, "outputContractIssues": 0}
        # 多父 DAG(nodeOutputKeys)→ relations 全保留
        edges = {(e["src_id"], e["dst_id"]) for e in g["relations"]}
        assert edges == {("scope-decompose", "analysis"),
                         ("scope-decompose", "report"),
                         ("analysis", "report")}
        assert all(e["type"] == "DEPENDENCY" for e in g["relations"])

    def test_top_status_maps_flow_runs_status_to_task_enum(self):
        """task_callback.status 落映射后的 task Status 枚举(非原始 ClawMind 字符串)。
        ClawMind flow_runs.status 仅 7 个枚举,按语义对应 task 7 态。"""
        cases = {
            "running": "RUNNING",
            "succeeded": "DONE",
            "failed": "FAILED",
            "cancelled": "CANCELLED",
            "waiting": "PENDING",
            "aborted": "CANCELLED",
            "blocked": "PENDING",
        }
        for src, want in cases.items():
            body = {"workflow_id": "w", "flow_id": "f", "status": src,
                    "ext_info": {"flow_runs": {"status": src, "origin_session_id": "S"},
                                 "node_executions": []}}
            d = translate_claw_mind(body, "result").data.data
            assert d["status"] == want, (
                f"flow_runs.status={src!r} → task_callback.status={d['status']!r}, want {want!r}")

    def test_execution_graph_status_mapping(self):
        cases = {
            "succeeded": "DONE", "completed": "DONE", "node_succeeded": "DONE", "success": "DONE",
            "failed": "FAILED", "node_failed": "FAILED",
            "cancelled": "CANCELLED", "canceled": "CANCELLED", "aborted": "CANCELLED",
            "blocked": "PENDING", "waiting": "PENDING",
            "running": "RUNNING", "started": "RUNNING", "in_progress": "RUNNING",
            "": "PENDING", "unknown_xyz": "PENDING",
        }
        for src, want in cases.items():
            body = {"workflow_id": "w", "flow_id": "f", "status": src,
                    "ext_info": {"flow_runs": {"id": 1, "status": src,
                                               "result_json": "{}", "params_json": "{}"},
                                 "node_executions": []}}
            g = _claw_mind_graph(body, "result")
            assert g["status"] == want, f"{src!r} → {g['status']!r}, want {want!r}"
            assert g["run_id"] == 1

    def test_execution_graph_node_failed_status_and_error(self):
        body = {"workflow_id": "w", "flow_id": "f", "status": "node_failed",
                "ext_info": {"flow_runs": {"origin_session_id": "S-1", "status": "failed"},
                             "node_executions": [{"node_id": "N1", "status": "failed",
                                                  "error_text": "boom", "node_title": "调研拆题"}]}}
        g = _claw_mind_graph(body, "result")
        assert g["status"] == "FAILED"
        assert g["tasks"][0]["status"] == "FAILED"
        assert g["tasks"][0]["task_spec"]["metadata"]["title"] == "调研拆题"
        assert g["tasks"][0]["run_info"]["extend_props"]["error_text"] == "boom"

    def test_execution_graph_filters_dangling_edges(self):
        # nodeOutputKeys 引用未执行的节点 → 悬挂边被过滤
        body = {"workflow_id": "w", "flow_id": "f", "status": "succeeded",
                "ext_info": {"flow_runs": {"status": "succeeded"},
                             "node_executions": [{"node_id": "n", "status": "succeeded",
                                 "input_json": '{"params":{},"nodeOutputKeys":["ghost","n2"]}'}]}}
        g = _claw_mind_graph(body, "result")
        assert g["relations"] == []
        assert len(g["tasks"]) == 1

    def test_execution_graph_empty_ext_returns_none(self):
        body = {"workflow_id": "w", "flow_id": "f", "status": "started", "ext_info": {}}
        assert _claw_mind_graph(body, "start") is None


class TestBCN:
    """BCN(BCS Group)CloudEvent 回调解析(语雀《BCS Group 回调接入说明》)。

    仅处理 state_machine.run.created/started、node.started/completed、run.completed 五个事件。
    """

    _EVT = {
        "spec_version": "1.0", "event_id": "evt-1",
        "event_type": "state_machine.node.completed", "source": "bcs",
        "scope": {"group_id": "g1", "session_id": "s-1", "run_id": "run-1"},
        "stream": {"key": "state-machine-run:run-1", "sequence": 6},
        "actor": {"type": "bot", "id": "b1"},
        "data": {"run_id": "run-1", "node_id": "N1", "attempt": 1, "outcome": "success",
                 "output": {"answer": 7}, "completed_at": "t", "duration_ms": 100},
    }

    def test_is_bcn_event_payload(self):
        assert is_bcn_event_payload(self._EVT) is True
        e = dict(self._EVT)
        del e["scope"]
        assert is_bcn_event_payload(e) is False
        assert is_bcn_event_payload(None) is False

    def test_node_completed_maps_to_result(self):
        tc = translate_bcn(self._EVT)
        assert tc.disposition == "result"
        d = tc.data.data
        assert d["loop_task_id"] == "run-1"             # req4:node_id 恒空,= run_id
        assert d["workflow_source"] == "bcn"
        assert d["workflow_instance_id"] == "s-1"          # scope.session_id → main_session_id
        assert d["event_id"] == "evt-1"                    # CloudEvent 幂等键透传
        assert d["status"] == "RUNNING"                     # req2:node.completed → RUNNING
        assert d["result"]["success"] is True
        assert d["result"]["data"] == {"answer": 7}
        # execution_graph 不在 translator 产物(由 CallbackDataEnricher.enrich_bcn 取 BCS run 明细后构建)
        assert "execution_graph" not in d
        assert d["_raw_callback_body"] == self._EVT

    def test_node_started_is_start_no_success(self):
        e = dict(self._EVT, event_type="state_machine.node.started")
        e["data"] = {"run_id": "run-1", "node_id": "N1", "attempt": 1, "started_at": "t"}
        tc = translate_bcn(e)
        assert tc.disposition == "start"
        assert tc.data.data["loop_task_id"] == "run-1"   # req4:node_id 恒空
        assert tc.data.data["status"] == "RUNNING"        # req2:node.started → RUNNING
        assert "success" not in tc.data.data["result"]     # started 不设 success

    def test_run_started_and_created_have_no_node_id(self):
        e = dict(self._EVT, event_type="state_machine.run.started")
        e["data"] = {"run_mode": "configured", "started_at": "t"}
        tc = translate_bcn(e)
        assert tc.disposition == "start"
        assert tc.data.data["loop_task_id"] == "run-1"     # 无 node_id(req4 恒空)
        assert tc.data.data["status"] == "RUNNING"          # req2:run.started → RUNNING
        ec = dict(self._EVT, event_type="state_machine.run.created")
        ec["data"] = {"definition_id": "d", "status": "running"}
        assert translate_bcn(ec).disposition == "start"

    def test_run_completed_is_result_no_node(self):
        e = dict(self._EVT, event_type="state_machine.run.completed")
        e["data"] = {"completed_at": "t", "output": {"final": True}, "duration_ms": 9}
        tc = translate_bcn(e)
        assert tc.disposition == "result"
        assert tc.data.data["loop_task_id"] == "run-1"
        assert tc.data.data["status"] == "DONE"           # req2:run.completed → DONE
        assert tc.data.data["result"]["success"] is True
        assert tc.data.data["result"]["data"] == {"final": True}
        # execution_graph 不在 translator 产物(极简兜底由 CallbackDataEnricher.enrich_bcn 构建)
        assert "execution_graph" not in tc.data.data

    def test_node_completed_failed_outcome(self):
        e = dict(self._EVT, event_type="state_machine.node.completed")
        e["data"] = {"run_id": "run-1", "node_id": "N1", "outcome": "failed", "reason": "boom"}
        d = translate_bcn(e).data.data
        assert d["status"] == "RUNNING"                   # req2:node.completed → RUNNING(失败态由结果/收敛处理)
        assert d["result"]["success"] is False
        assert d["result"]["exec_error"] == "boom"

    def test_non_handled_event_returns_none(self):
        for et in ("group.created", "session.created", "message.created",
                   "state_machine.node.retry_scheduled"):
            assert translate_bcn(dict(self._EVT, event_type=et)) is None