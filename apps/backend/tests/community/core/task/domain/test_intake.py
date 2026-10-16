from agentclaw.community.core.task.domain.intake import missing_intake_fields
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    TaskSpec,
    TaskSourceType,
)
from agentclaw.community.core.task.domain.requests import TaskInfoRequest
from agentclaw.community.core.task.domain.intake import (
    IntakeTarget,
    IntakeTargetType,
    TaskExecutionMode,
    TaskIntakeResult,
    TaskIntakeStatus,
)
from agentclaw.community.core.task.task_center.task_intake import build_task_info_request


def _spec(*, constraints_present: bool) -> TaskSpec:
    props = {"deliverables": ["上线记录"]}
    if constraints_present:
        props["constraints"] = []
    return TaskSpec(
        context=Context(title="上线风控模型", extend_props=props),
        goal=Goal(
            objective="上线风控模型",
            acceptances=[AcceptanceCriteria(id="ac1", description="灰度达到 10%")],
        ),
    )


def test_constraints_must_be_explicitly_clarified() -> None:
    assert missing_intake_fields(_spec(constraints_present=False)) == ["constraints"]
    assert missing_intake_fields(_spec(constraints_present=True)) == []


def test_missing_spec_requires_all_four_elements() -> None:
    assert missing_intake_fields(None) == [
        "objective", "deliverables", "acceptances", "constraints"
    ]


def test_confirmed_spec_can_be_used_directly_by_execute_request() -> None:
    spec = _spec(constraints_present=True)
    request = TaskInfoRequest(
        task_spec=spec,
        source_type=TaskSourceType.API,
        owner_user_id="owner-1",
        owner_bot_id="bot-1",
    )
    task_info = request.to_task_info("task-1")
    assert task_info.task_spec is spec
    assert task_info.task_id == "task-1"


def test_confirmed_intake_maps_frozen_execution_mode() -> None:
    result = TaskIntakeResult(
        session_id="tis-1",
        owner_user_id="owner-1",
        source_type=TaskSourceType.API,
        status=TaskIntakeStatus.CONFIRMED,
        target=IntakeTarget(IntakeTargetType.BOT, "bot-1"),
        execution_mode=TaskExecutionMode.CENTRALIZED,
        task_spec=_spec(constraints_present=True),
    )
    request = build_task_info_request(result)
    assert request.execution_config["orchestration_mode"] == "centralized"
