from agentclaw.community.core.task.domain.models import TaskSourceType, TaskType
from agentclaw.community.core.task.domain.requests import (
    RequestAcceptance, RequestContext, RequestGoal, RequestMetadata,
    RequestTaskSpec, TaskInfoRequest,
)


def _request() -> TaskInfoRequest:
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(title="T", instruction="do"),
            context=RequestContext(background="bg", extend_props={"k": 1}),
            goal=RequestGoal(objective="o", acceptances=[RequestAcceptance(id="ac1", acceptance="acc-text")]),
        ),
        source_type=TaskSourceType.COOP_GROUP,
        owner_user_id="U1",
        owner_bot_id="B1",
        execution_config={"task_type": TaskType.WORKFLOW, "workflow_id": "wf-1"},
    )


def test_to_task_info_maps_fields_and_acceptance_to_description():
    ti = _request().to_task_info("tid-123")
    m = ti.task_spec.metadata
    assert m.task_id == "tid-123"
    assert m.title == "T" and m.instruction == "do"
    assert ti.task_spec.context.background == "bg"
    assert ti.task_spec.context.extend_props == {"k": 1}
    assert ti.task_spec.goal.objective == "o"
    assert ti.task_spec.goal.acceptances[0].id == "ac1"
    assert ti.task_spec.goal.acceptances[0].description == "acc-text"  # acceptance → description
    assert ti.source_type == "coop_group"          # source_type.value
    assert ti.owner_bot_id == "B1"                    # owner_bot_id (D3)
    assert ti.execution_config["workflow_id"] == "wf-1"


def test_task_spec_to_dict_is_domain_shape():
    ti = _request().to_task_info("tid-123")
    d = ti.task_spec.to_dict()
    assert d["metadata"] == {"task_id": "tid-123", "title": "T", "instruction": "do"}
    assert d["context"] == {"background": "bg", "extend_props": {"k": 1}}
    assert d["goal"]["objective"] == "o"
    assert d["goal"]["acceptances"] == [{"id": "ac1", "description": "acc-text"}]


def test_enums_values():
    assert {e.value for e in TaskSourceType} == {"bot", "coop_group", "api"}
    assert {e.value for e in TaskType} == {"yaml", "workflow", "dynamic"}
