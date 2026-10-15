"""Wire contract for the two-event Relay report interface."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentclaw.community.adapters.http.task.schemas import (
    AcceptanceResultDTO,
    GoalDTO,
    TaskSpecDTO,
    acceptance_result_from_dto,
    task_spec_from_dto,
)
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Goal,
    RuntimeInfo,
    TaskNode,
    TaskNodePatch,
    TaskReportCommand,
    TaskReportTrace,
    TaskReportTraceStep,
    TaskReportEventType,
    TaskReportResult,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    TrajectoryActionType,
)


class ExecutionEventDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    node_id: str | None = None
    execution_decision: str
    actual_goal: GoalDTO | None = None
    output_patch: dict[str, Any] = Field(default_factory=dict)
    local_acceptance_result: AcceptanceResultDTO | None = None
    failure_reason: str | None = None


class PlannedNodeRunInfoDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_mode: str
    assignee: str | None = None
    failure_reason: str | None = None
    extend_props: dict[str, Any] = Field(default_factory=dict)


class PlannedNodeDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    node_id: None = None
    status: None = None
    task_spec: TaskSpecDTO
    run_info: PlannedNodeRunInfoDTO
    node_run_graph: None = None


class TaskReportTraceStepDTO(BaseModel):
    """单条 trace step——七字段**全键必填**(2026-10-15 收紧契约):

    ``action_type|action_input|action_result|status_from|status_to|boost_reason|
    error_msg`` 一键不可缺(值可为 None);``action_type`` 必须为轨迹动作枚举值
    (``submit|plan|dispatch|execute|verify|reset|transition|relay``)——读侧
    枚举强校验会丢弃未知值,入口先拦并点名。
    """

    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(min_length=1)
    action_input: str | None
    action_result: str = Field(min_length=1)
    status_from: str | None
    status_to: str | None
    boost_reason: str | None
    error_msg: str | None

    @field_validator("action_type")
    @classmethod
    def _action_type_in_trajectory_vocab(cls, value: str) -> str:
        allowed = "|".join(member.value for member in TrajectoryActionType)
        try:
            TrajectoryActionType(value)
        except ValueError:
            raise ValueError(
                f"action_type 必须为轨迹动作枚举值之一: {allowed}"
            ) from None
        return value


class TaskReportTraceDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[TaskReportTraceStepDTO]


class TaskReportCommandDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    relay_token: str = Field(min_length=1)
    event_type: TaskReportEventType
    event: dict[str, Any] | None = None
    progress_reason: str = ""
    failure_reason: str | None = None
    task_acceptance_result: AcceptanceResultDTO | None = None
    trace: TaskReportTraceDTO

    def to_domain(self) -> TaskReportCommand:
        if self.event_type == TaskReportEventType.EXECUTION_RESULT:
            if self.event is None:
                raise TaskStateError("EXECUTION_RESULT requires an event")
            parsed = ExecutionEventDTO.model_validate(self.event)
            if parsed.task_id is not None and parsed.task_id != self.task_id:
                raise TaskStateError("execution patch belongs to another task")
            if parsed.node_id is not None and parsed.node_id != self.node_id:
                raise TaskStateError("execution patch targets another node")
            goal = parsed.actual_goal
            event: TaskNodePatch | TaskNode | None = TaskNodePatch(
                task_id=self.task_id,
                node_id=self.node_id,
                execution_decision=parsed.execution_decision,
                actual_goal=(
                    Goal(
                        goal.objective,
                        [
                            AcceptanceCriteria(item.id, item.description)
                            for item in goal.acceptances
                        ],
                    )
                    if goal
                    else None
                ),
                output_patch=dict(parsed.output_patch),
                local_acceptance_result=(
                    acceptance_result_from_dto(parsed.local_acceptance_result)
                    if parsed.local_acceptance_result
                    else None
                ),
                failure_reason=parsed.failure_reason,
            )
        elif self.event is not None:
            planned = PlannedNodeDTO.model_validate(self.event)
            if planned.task_id is not None and planned.task_id != self.task_id:
                raise TaskStateError("planned node belongs to another task")
            event = TaskNode(
                node_id=None,
                task_id=self.task_id,
                status=None,
                task_spec=task_spec_from_dto(planned.task_spec),
                run_info=RuntimeInfo(
                    run_mode=planned.run_info.run_mode,
                    assignee=planned.run_info.assignee,
                    failure_reason=planned.run_info.failure_reason,
                    extend_props=dict(planned.run_info.extend_props),
                ),
                node_run_graph=None,
            )
        else:
            event = None
        return TaskReportCommand(
            task_id=self.task_id,
            node_id=self.node_id,
            event_id=self.event_id,
            relay_token=self.relay_token,
            event_type=self.event_type,
            event=event,
            progress_reason=self.progress_reason,
            failure_reason=self.failure_reason,
            task_acceptance_result=(
                acceptance_result_from_dto(self.task_acceptance_result)
                if self.task_acceptance_result
                else None
            ),
            trace=TaskReportTrace(
                steps=[
                    TaskReportTraceStep(
                        action_type=item.action_type,
                        action_result=item.action_result,
                        action_input=item.action_input,
                        status_from=item.status_from,
                        status_to=item.status_to,
                        boost_reason=item.boost_reason,
                        error_msg=item.error_msg,
                    )
                    for item in self.trace.steps
                ],
            )
            if self.trace is not None
            else None,
        )


class TaskReportResultDTO(BaseModel):
    status: str
    target_node_id: str | None = None
    task_completed: bool = False
    task_hung: bool = False
    bbs_publish_pending: bool = False


def report_result_to_dto(result: TaskReportResult) -> TaskReportResultDTO:
    return TaskReportResultDTO(
        status=result.status.value,
        target_node_id=result.target_node_id,
        task_completed=result.task_completed,
        task_hung=result.task_hung,
        bbs_publish_pending=result.bbs_publish_pending,
    )
