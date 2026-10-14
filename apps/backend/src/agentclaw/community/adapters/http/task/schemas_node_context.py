"""Node-scoped S1 context wire projection for distributed relay execution."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.task.schemas import (
    AcceptanceCriteriaDTO,
    AcceptanceResultDTO,
    ContextDTO,
    DoneOutputDTO,
    GoalDTO,
    TaskSpecDTO,
    task_context_to_dto,
)
from agentclaw.community.core.task.domain.models import Goal, TaskContext, TaskSpec


class AcceptedExecutionPatchDTO(BaseModel):
    task_id: str
    node_id: str
    execution_decision: str
    actual_goal: GoalDTO | None = None
    output_patch: dict[str, Any] = Field(default_factory=dict)
    local_acceptance_result: AcceptanceResultDTO | None = None
    failure_reason: str | None = None


class AcceptedPlanReportDTO(BaseModel):
    status: str
    target_node_id: str | None = None
    task_completed: bool = False
    task_hung: bool = False
    bbs_publish_pending: bool = False


class NodeTaskContextDTO(BaseModel):
    task_id: str
    node_id: str
    root_task_spec: TaskSpecDTO
    current_task_spec: TaskSpecDTO
    actual_goal: GoalDTO | None = None
    capability_failure_reason: str | None = None
    all_done_output: list[DoneOutputDTO] = Field(default_factory=list)
    accepted_execution_patch: AcceptedExecutionPatchDTO | None = None
    accepted_plan_report: AcceptedPlanReportDTO | None = None


def _goal_to_dto(goal: Goal) -> GoalDTO:
    return GoalDTO(
        objective=goal.objective,
        acceptances=[
            AcceptanceCriteriaDTO(id=item.id, description=item.description)
            for item in goal.acceptances
        ],
    )


def _spec_to_dto(spec: TaskSpec) -> TaskSpecDTO:
    return TaskSpecDTO(
        context=ContextDTO(
            title=spec.context.title,
            background=spec.context.background,
            extend_props=dict(spec.context.extend_props),
        ),
        goal=_goal_to_dto(spec.goal),
    )


def node_context_to_dto(context: TaskContext) -> NodeTaskContextDTO:
    if not all(
        (
            context.task_id,
            context.node_id,
            context.root_task_spec,
            context.current_task_spec,
        )
    ):
        raise ValueError("node-scoped TaskContext is incomplete")
    patch = context.accepted_execution_patch
    patch_dto = None
    if patch is not None:
        acceptance = patch.local_acceptance_result
        patch_dto = AcceptedExecutionPatchDTO(
            task_id=patch.task_id,
            node_id=patch.node_id,
            execution_decision=patch.execution_decision or "",
            actual_goal=_goal_to_dto(patch.actual_goal) if patch.actual_goal else None,
            output_patch=dict(patch.output_patch or {}),
            local_acceptance_result=(
                AcceptanceResultDTO(
                    verdict=acceptance.verdict.value,
                    done_items=list(acceptance.done_items),
                    gap_items=list(acceptance.gap_items),
                )
                if acceptance
                else None
            ),
            failure_reason=patch.failure_reason,
        )
    return NodeTaskContextDTO(
        task_id=context.task_id,
        node_id=context.node_id,
        root_task_spec=_spec_to_dto(context.root_task_spec),
        current_task_spec=_spec_to_dto(context.current_task_spec),
        actual_goal=_goal_to_dto(context.actual_goal) if context.actual_goal else None,
        capability_failure_reason=context.capability_failure_reason,
        all_done_output=task_context_to_dto(context).all_done_output,
        accepted_execution_patch=patch_dto,
        accepted_plan_report=(
            AcceptedPlanReportDTO(
                status=context.accepted_plan_report.status.value,
                target_node_id=context.accepted_plan_report.target_node_id,
                task_completed=context.accepted_plan_report.task_completed,
                task_hung=context.accepted_plan_report.task_hung,
                bbs_publish_pending=context.accepted_plan_report.bbs_publish_pending,
            )
            if context.accepted_plan_report
            else None
        ),
    )
