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
        assert tc.disposition == "result"
        d = tc.data.data
        assert d["loop_task_id"] == "risk-review-pipeline"  # loop_task_id = workflow_id;node_id 空
        assert d["workflow_source"] == "claw_mind"
        assert d["workflow_instance_id"] == "S-9"        # session_id = origin_session_id
        assert d["status"] == "succeeded"                # 从底层 flow_runs.status 推(非顶层 node_succeeded)
        assert d["result"]["success"] is True
        assert d["result"]["data"] == {"answer": 42}
        assert d["execution_graph"] == self._BODY["ext_info"]   # 全量 ext_info → execution_graph
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
        assert d["status"] == "failed"
        assert d["workflow_instance_id"] == "S-1"
        assert d["result"]["success"] is False
        assert d["result"]["exec_error"] == "boom"

    def test_translate_claw_mind_top_status_fallback_unknown_success(self):
        # 无 flow_runs.status / node_executions → status 退顶层;未知 status 不设 success
        body = {"workflow_id": "w", "flow_id": "f", "status": "started", "ext_info": {}}
        d = translate_claw_mind(body, "start").data.data
        assert d["status"] == "started"
        assert "success" not in d["result"]

    def test_translate_claw_mind_session_id_empty_when_absent(self):
        body = {"workflow_id": "w", "flow_id": "f", "status": "succeeded",
                "ext_info": {"flow_runs": {"status": "succeeded"}}}
        assert translate_claw_mind(body, "result").data.data["workflow_instance_id"] == ""


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
        assert d["loop_task_id"] == "run-1::N1"
        assert d["workflow_source"] == "bcn"
        assert d["workflow_instance_id"] == "s-1"          # scope.session_id → main_session_id
        assert d["status"] == "state_machine.node.completed"
        assert d["result"]["success"] is True
        assert d["result"]["data"] == {"answer": 7}
        assert d["execution_graph"] == self._EVT["data"]
        assert d["_raw_callback_body"] == self._EVT

    def test_node_started_is_start_no_success(self):
        e = dict(self._EVT, event_type="state_machine.node.started")
        e["data"] = {"run_id": "run-1", "node_id": "N1", "attempt": 1, "started_at": "t"}
        tc = translate_bcn(e)
        assert tc.disposition == "start"
        assert tc.data.data["loop_task_id"] == "run-1::N1"
        assert "success" not in tc.data.data["result"]     # started 不设 success

    def test_run_started_and_created_have_no_node_id(self):
        e = dict(self._EVT, event_type="state_machine.run.started")
        e["data"] = {"run_mode": "configured", "started_at": "t"}
        tc = translate_bcn(e)
        assert tc.disposition == "start"
        assert tc.data.data["loop_task_id"] == "run-1"     # 无 node_id
        ec = dict(self._EVT, event_type="state_machine.run.created")
        ec["data"] = {"definition_id": "d", "status": "running"}
        assert translate_bcn(ec).disposition == "start"

    def test_run_completed_is_result_no_node(self):
        e = dict(self._EVT, event_type="state_machine.run.completed")
        e["data"] = {"completed_at": "t", "output": {"final": True}, "duration_ms": 9}
        tc = translate_bcn(e)
        assert tc.disposition == "result"
        assert tc.data.data["loop_task_id"] == "run-1"
        assert tc.data.data["result"]["success"] is True
        assert tc.data.data["result"]["data"] == {"final": True}

    def test_node_completed_failed_outcome(self):
        e = dict(self._EVT, event_type="state_machine.node.completed")
        e["data"] = {"run_id": "run-1", "node_id": "N1", "outcome": "failed", "reason": "boom"}
        d = translate_bcn(e).data.data
        assert d["result"]["success"] is False
        assert d["result"]["exec_error"] == "boom"

    def test_non_handled_event_returns_none(self):
        for et in ("group.created", "session.created", "message.created",
                   "state_machine.node.retry_scheduled"):
            assert translate_bcn(dict(self._EVT, event_type=et)) is None