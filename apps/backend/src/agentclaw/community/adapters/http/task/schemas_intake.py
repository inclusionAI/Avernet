"""HTTP DTOs for clarification and the separate confirmed-task start call."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.task.schemas import (
    ContextDTO, GoalDTO, TaskSpecDTO,
)
from agentclaw.community.core.task.domain.intake import (
    IntakeTarget, IntakeTargetType, TaskIntakeAction, TaskIntakeCommand,
    TaskIntakeResult, TaskIntakeStatus,
)


class IntakeTargetDTO(BaseModel):
    target_type: IntakeTargetType
    target_id: str = Field(min_length=1)


class TaskIntakeCommandDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: TaskIntakeAction
    message: str | None = None
    requested_target: IntakeTargetDTO | None = None

    def to_domain(self) -> TaskIntakeCommand:
        target = self.requested_target
        return TaskIntakeCommand(
            action=self.action,
            message=self.message,
            requested_target=(
                IntakeTarget(target.target_type, target.target_id) if target else None
            ),
        )


class TaskIntakeResultDTO(BaseModel):
    session_id: str
    owner_user_id: str
    source_type: str
    status: TaskIntakeStatus
    target: IntakeTargetDTO | None = None
    task_spec: TaskSpecDTO | None = None
    missing_fields: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    create_time_ms: int
    update_time_ms: int


class StartIntakeRequestDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)


def intake_result_to_dto(result: TaskIntakeResult) -> TaskIntakeResultDTO:
    spec = result.task_spec
    return TaskIntakeResultDTO(
        session_id=result.session_id,
        owner_user_id=result.owner_user_id,
        source_type=result.source_type.value,
        status=result.status,
        target=(IntakeTargetDTO(
            target_type=result.target.target_type, target_id=result.target.target_id,
        ) if result.target else None),
        task_spec=(TaskSpecDTO(
            context=ContextDTO(
                title=spec.context.title,
                background=spec.context.background,
                extend_props=dict(spec.context.extend_props),
            ),
            goal=GoalDTO(
                objective=spec.goal.objective,
                acceptances=[
                    {"id": item.id, "description": item.description}
                    for item in spec.goal.acceptances
                ],
            ),
        ) if spec else None),
        missing_fields=list(result.missing_fields),
        questions=list(result.questions),
        create_time_ms=result.create_time_ms,
        update_time_ms=result.update_time_ms,
    )
