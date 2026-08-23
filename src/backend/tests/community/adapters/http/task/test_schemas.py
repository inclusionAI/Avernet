import pytest
from pydantic import ValidationError

from agentclaw.community.adapters.http.task.schemas import (
    TaskCallbackRequest, TaskNodeCallbackRequest, op_result_to_dto,
    runtime_status_to_product_status,
)
from agentclaw.community.core.task.domain.models import TaskOpResult


def _base(**kw):
    d = dict(task_id="t1", workflow_source="bcn", workflow_id="w1",
             workflow_instance_id="i1", status="COMPLETED", is_success=True)
    d.update(kw)
    return d


def test_task_callback_request_defaults():
    r = TaskCallbackRequest(**_base())
    assert r.goal is None and r.output is None and r.failed_info is None
    assert r.ext_info is None and r.loop_task_id is None


def test_node_callback_request_requires_node_id():
    with pytest.raises(ValidationError):
        TaskNodeCallbackRequest(**_base())  # 缺 node_id
    r = TaskNodeCallbackRequest(**_base(node_id="n1"))
    assert r.node_id == "n1"


def test_workflow_source_literal():
    with pytest.raises(ValidationError):
        TaskCallbackRequest(**_base(workflow_source="bbs"))


def test_required_fields_enforced():
    with pytest.raises(ValidationError):
        TaskCallbackRequest(task_id="t1", workflow_source="bcn")  # 缺必填


def test_op_result_to_dto_returns_extend_props():
    dto = op_result_to_dto(TaskOpResult(
        task_id="t1",
        success=True,
        run_id=1,
        extend_props={"group_id": "bcs_grp_1"},
    ))

    assert dto.extend_props == {"group_id": "bcs_grp_1"}


@pytest.mark.parametrize(
    ("runtime", "product"),
    [
        ("PENDING", "EXECUTING"),
        ("PLANNING", "EXECUTING"),
        ("RUNNING", "EXECUTING"),
        ("HUNG", "REVIEWING"),
        ("DONE", "DONE"),
        ("FAILED", "FAILED"),
        ("CANCELLED", "CANCELLED"),
    ],
)
def test_runtime_status_to_product_status(runtime, product):
    assert runtime_status_to_product_status(runtime) == product
