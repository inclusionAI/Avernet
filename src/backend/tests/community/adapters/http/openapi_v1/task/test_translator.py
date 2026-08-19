import pytest

from agentclaw.community.adapters.http.openapi_v1.task.schemas import (
    TaskCallbackRequest, TaskNodeCallbackRequest,
)
from agentclaw.community.adapters.http.openapi_v1.task.translator import translate
from agentclaw.community.core.errors import CallbackCorrelationError
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
        assert tc.data.loop_task_id == "t1::c1"

    def test_task_level_uses_echo_loop_task_id(self):
        req = TaskCallbackRequest(**_base(loop_task_id="t1::root1"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.loop_task_id == "t1::root1"

    def test_task_level_no_echo_resolves_via_registry(self):
        reg = _reg_with()
        req = TaskCallbackRequest(**_base())  # 无 loop_task_id 回声
        tc = translate(req, "result", reg)
        assert tc.data.loop_task_id == "t1::root1"

    def test_task_level_no_echo_no_registry_raises(self):
        req = TaskCallbackRequest(**_base())
        with pytest.raises(CallbackCorrelationError):
            translate(req, "result", InMemoryCallbackCorrelationRegistry())


class TestFieldFolding:
    def test_success_folds_output_to_result_data(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", output={"r": 1}, is_success=True))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.result["success"] is True
        assert tc.data.result["data"] == {"r": 1}
        assert "fail_detail" not in tc.data.result

    def test_fail_folds_failed_info_to_fail_detail(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", is_success=False, failed_info="boom"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.result["success"] is False
        assert tc.data.result["fail_detail"] == "boom"

    def test_ext_info_and_goal_folded_into_ext(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", ext_info={"k": "v"}, goal="G"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        ext = tc.data.result["_ext_info"]
        assert ext["k"] == "v"
        assert ext["_callback_goal"] == "G"

    def test_workflow_source_maps_to_workflow_type(self):
        req = TaskNodeCallbackRequest(**_base(workflow_source="claw_mind", node_id="c1"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.workflow_type == "single_bot"

    def test_registry_provides_ssot_int_ids(self):
        reg = _reg_with()
        req = TaskCallbackRequest(**_base(loop_task_id="t1::root1"))
        tc = translate(req, "result", reg)
        assert tc.data.workflow_id == 7
        assert tc.data.instance_id == 77

    def test_unregistered_node_level_falls_back_zero_int_and_stashes_str(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.workflow_id == 0
        assert tc.data.result["_ext_info"]["_workflow_id_str"] == "w7"
        assert tc.data.result["_ext_info"]["_instance_id_str"] == "i1"


class TestDisposition:
    def test_result_disposition_passes_through(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1"))
        assert translate(req, "result", InMemoryCallbackCorrelationRegistry()).disposition == "result"

    def test_start_disposition_passes_through(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", status="RUNNING"))
        assert translate(req, "start", InMemoryCallbackCorrelationRegistry()).disposition == "start"