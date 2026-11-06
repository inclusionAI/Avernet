"""Task HTTP requests ignore unknown keys without relaxing known-field contracts."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from agentclaw.community.adapters.http.task.schemas import (
    TaskGrantRequestDTO,
    TaskRevokeRequestDTO,
    TaskSettingRequestDTO,
)
from agentclaw.community.adapters.http.task.schemas_artifact import (
    TaskArtifactAddRequestDTO,
)
from agentclaw.community.adapters.http.task.schemas_intake import (
    TaskIntakeCommandDTO,
    StartIntakeRequestDTO,
)
from agentclaw.community.adapters.http.task.schemas_relay_dispatch import (
    TaskDispatchRequestDTO,
)
from agentclaw.community.adapters.http.task.schemas_relay_search import (
    TaskSearchRequestDTO,
)
from agentclaw.community.adapters.http.task.schemas_relay_report import (
    ArtifactReportItemDTO,
    ExecutionEventDTO,
    PlannedNodeDTO,
    PlannedNodeRunInfoDTO,
    TaskReportCommandDTO,
    TaskReportTraceDTO,
    TaskReportTraceStepDTO,
)

IDENTITY = {"task_id": "t1", "node_id": "n1", "relay_token": "generation-1"}
STEP = {
    "action_type": "S1_CONTEXT",
    "action_input": None,
    "action_result": '{"status":"completed","output":"ok"}',
    "status_from": None,
    "status_to": None,
    "boost_reason": None,
    "error_msg": None,
}
SPEC = {
    "context": {"title": "next"},
    "goal": {"objective": "complete", "acceptances": []},
}
ARTIFACT = {
    "artifact_name": "result",
    "artifact_kind": "text",
    "task_id": "t1",
    "node_id": "n1",
    "attempt": 0,
    "text_content": {"text": "result"},
}
CASES = [
    (TaskSearchRequestDTO, {**IDENTITY, "query": "query"}),
    (
        TaskDispatchRequestDTO,
        {
            "task_id": "t1",
            "origin_node_id": "n1",
            "target_node_id": "n2",
            "relay_token": "generation-1",
        },
    ),
    (TaskIntakeCommandDTO, {"action": "START", "message": "goal"}),
    (StartIntakeRequestDTO, {"session_id": "session-1"}),
    (TaskGrantRequestDTO, {"bcs_bot_id": "bot:owner"}),
    (TaskRevokeRequestDTO, {"bcs_bot_id": "bot:owner"}),
    (TaskSettingRequestDTO, {"setting_type": "relay_execution", "enabled": True}),
    (TaskArtifactAddRequestDTO, ARTIFACT),
    (ArtifactReportItemDTO, ARTIFACT),
    (
        ExecutionEventDTO,
        {"execution_decision": "DECLINED", "failure_reason": "not capable"},
    ),
    (PlannedNodeRunInfoDTO, {"run_mode": "single_bot", "assignee": "bot:owner"}),
    (PlannedNodeDTO, {"task_spec": SPEC, "run_info": {"run_mode": "single_bot"}}),
    (
        TaskReportCommandDTO,
        {
            **IDENTITY,
            "event_id": "event-1",
            "event_type": "EXECUTION_RESULT",
            "event": {"execution_decision": "DECLINED"},
        },
    ),
    (TaskReportTraceDTO, {"steps": [STEP]}),
    (TaskReportTraceStepDTO, STEP),
]


@pytest.mark.parametrize("model,body", CASES, ids=[m.__name__ for m, _ in CASES])
@pytest.mark.parametrize("extra", [None, "extra", {"nested": [1, 2]}])
def test_unknown_fields_are_discarded(model, body, extra):
    baseline = model.model_validate(deepcopy(body)).model_dump()
    parsed = model.model_validate({**deepcopy(body), "future_parameter": extra})
    assert parsed.model_dump() == baseline
    assert parsed.model_extra in (None, {})
    assert not hasattr(parsed, "future_parameter")


@pytest.mark.parametrize("model,body", CASES, ids=[m.__name__ for m, _ in CASES])
def test_unknown_fields_do_not_replace_required_fields(model, body):
    required = [
        name for name, field in model.model_fields.items() if field.is_required()
    ]
    assert required
    for name in required:
        incomplete = {**deepcopy(body), "future_parameter": "ignored"}
        incomplete.pop(name)
        with pytest.raises(ValidationError) as exc:
            model.model_validate(incomplete)
        assert any(
            e["type"] == "missing" and e["loc"] == (name,) for e in exc.value.errors()
        )


@pytest.mark.parametrize(
    "model,body,field,value",
    [
        (TaskSearchRequestDTO, {**IDENTITY, "query": "q"}, "query", " "),
        (
            TaskDispatchRequestDTO,
            {
                "task_id": "t1",
                "origin_node_id": "n1",
                "target_node_id": "n2",
                "relay_token": "generation-1",
            },
            "target_node_id",
            "",
        ),
        (StartIntakeRequestDTO, {"session_id": "s1"}, "session_id", ""),
        (TaskIntakeCommandDTO, {"action": "START"}, "action", "INVALID"),
        (
            TaskSettingRequestDTO,
            {"setting_type": "relay_execution", "enabled": True},
            "enabled",
            {},
        ),
        (
            PlannedNodeDTO,
            {"task_spec": SPEC, "run_info": {"run_mode": "single_bot"}},
            "node_id",
            "client-created",
        ),
        (ArtifactReportItemDTO, ARTIFACT, "attempt", {}),
    ],
)
def test_invalid_known_fields_still_fail_with_extras(model, body, field, value):
    with pytest.raises(ValidationError):
        model.model_validate(
            {**deepcopy(body), field: value, "future_parameter": "ignored"}
        )


def test_known_viewer_fields_are_not_silently_ignored():
    for viewer in [
        {"viewer_actor_type": "bot"},
        {"viewer_actor_type": "admin", "viewer_actor_id": "id"},
        {"viewer_actor_type": "bot", "viewer_actor_id": " "},
    ]:
        with pytest.raises(ValidationError):
            TaskSearchRequestDTO.model_validate(
                {**IDENTITY, "query": "q", **viewer, "future_parameter": True}
            )


def test_nested_report_extras_do_not_change_domain_or_extension_data():
    body = {
        **IDENTITY,
        "event_id": "e1",
        "event_type": "PLAN_RESULT",
        "event": {
            "task_spec": deepcopy(SPEC),
            "run_info": {
                "run_mode": "single_bot",
                "assignee": "bot:owner",
                "extend_props": {"business_extension": {"key": "keep"}},
            },
        },
    }
    baseline = TaskReportCommandDTO.model_validate(body).to_domain()
    body["future_parameter"] = "ignored"
    body["event"]["future_parameter"] = "ignored"
    body["event"]["run_info"]["future_parameter"] = "ignored"
    parsed = TaskReportCommandDTO.model_validate(body).to_domain()
    assert parsed == baseline
    assert parsed.event.run_info.extend_props == {"business_extension": {"key": "keep"}}


def test_execution_output_and_runtime_extensions_are_preserved():
    from agentclaw.community.adapters.http.task.schemas import ExecutionConfigDTO
    from agentclaw.community.adapters.http.task.schemas_runtime_profile import (
        RuntimeProfileDTO,
    )

    output = {"future_parameter": {"business": "keep"}}
    parsed = ExecutionEventDTO.model_validate(
        {
            "execution_decision": "ACCEPTED",
            "output_patch": output,
            "future_parameter": "ignore envelope key only",
        }
    )
    assert parsed.output_patch == output
    assert (
        ExecutionConfigDTO.model_validate(
            {"task_type": "dynamic", **output}
        ).model_dump()["future_parameter"]
        == output["future_parameter"]
    )
    assert (
        RuntimeProfileDTO.model_validate(output).model_dump()["future_parameter"]
        == output["future_parameter"]
    )


def test_nested_trace_extras_do_not_change_report_command():
    actions = ("S1_CONTEXT", "S2_GAPS", "S3_CAPABILITY", "S4A_EXECUTION", "S4B_REPORT")
    body = {
        **IDENTITY,
        "event_id": "e1",
        "event_type": "EXECUTION_RESULT",
        "event": {"execution_decision": "DECLINED"},
        "trace": {"steps": [{**STEP, "action_type": action} for action in actions]},
    }
    baseline = TaskReportCommandDTO.model_validate(body).to_domain()
    body["trace"]["future_parameter"] = "ignored"
    for step in body["trace"]["steps"]:
        step["future_parameter"] = "ignored"
    assert TaskReportCommandDTO.model_validate(body).to_domain() == baseline
