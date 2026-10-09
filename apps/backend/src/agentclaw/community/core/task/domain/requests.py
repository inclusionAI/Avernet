"""Transport-agnostic execute request contract for collaboration tasks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    TaskInfo,
    TaskSpec,
)


@dataclass(frozen=True)
class RequestContext:
    title: str = ""
    background: str = ""
    extend_props: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RequestAcceptance:
    id: str
    acceptance: str


@dataclass(frozen=True)
class RequestGoal:
    objective: str
    acceptances: list[RequestAcceptance] = field(default_factory=list)


@dataclass(frozen=True)
class RequestTaskSpec:
    context: RequestContext
    goal: RequestGoal


@dataclass(frozen=True)
class SourceContext:
    """Task source/runtime facts supplied when a confirmed draft becomes a task."""

    source_type: "TaskSourceType"  # noqa: F821 — defined in models.py
    owner_user_id: str
    owner_bot_id: str
    execution_config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskInfoRequest:
    task_spec: RequestTaskSpec
    source_type: "TaskSourceType"  # noqa: F821 — defined in models.py
    owner_user_id: str
    owner_bot_id: str
    execution_config: dict[str, Any] = field(default_factory=dict)

    def to_task_info(self, task_id: str) -> TaskInfo:
        return TaskInfo(
            task_id=task_id,
            task_spec=TaskSpec(
                context=Context(
                    title=self.task_spec.context.title,
                    background=self.task_spec.context.background,
                    extend_props=dict(self.task_spec.context.extend_props),
                ),
                goal=Goal(
                    objective=self.task_spec.goal.objective,
                    acceptances=[
                        AcceptanceCriteria(id=a.id, description=a.acceptance)
                        for a in self.task_spec.goal.acceptances
                    ],
                ),
            ),
            source_type=self.source_type.value,
            owner_bot_id=self.owner_bot_id,
            owner_user_id=self.owner_user_id,
            execution_config=dict(self.execution_config),
        )


def init_task_request(
    task_info: dict[str, Any],
    source_context: SourceContext,
) -> TaskInfoRequest:
    """Convert the confirmed task-recognition draft into the stable execute contract.

    Clarification completeness is intentionally owned by task-loop. This function
    only performs the lossless field mapping and supplies empty defaults for the
    optional title/background/resources fields.
    """
    acceptance_values = list(task_info.get("acceptance_criteria") or [])
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            context=RequestContext(
                title=str(task_info.get("title") or ""),
                background=str(task_info.get("background") or ""),
                extend_props={
                    "deliverables": list(task_info.get("deliverables") or []),
                    "constraints": list(task_info.get("constraints") or []),
                    "resources": list(task_info.get("resources") or []),
                },
            ),
            goal=RequestGoal(
                objective=str(task_info.get("goal") or ""),
                acceptances=[
                    RequestAcceptance(id=f"ac{index + 1}", acceptance=str(value))
                    for index, value in enumerate(acceptance_values)
                ],
            ),
        ),
        source_type=source_context.source_type,
        owner_user_id=source_context.owner_user_id,
        owner_bot_id=source_context.owner_bot_id,
        execution_config=dict(source_context.execution_config),
    )
