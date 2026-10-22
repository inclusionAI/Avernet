"""Wire contract for the two-event Relay report interface."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
from agentclaw.community.core.task.task_context.task_artifact.report_item import (
    parse_artifact_report_items,
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


class ArtifactReportItemDTO(BaseModel):
    """一条上报产物 item:恰一个 content 字段(互斥)承载对应分支的 **JSON 对象**。

    content 对象不含 kind tag——判别由字段名承载;结构性校验(互斥/交叉校验/上限)
    在核心 ``parse_artifact_report_item`` 收口,HTTP 层只转协议(Rule 22)。
    content 对象形态为线上契约(2026-10-22 用户定稿);核心解析对 JSON 字符串
    仍容忍(手动 API 兼容),但本 DTO(线上口)只收对象。
    """

    model_config = ConfigDict(extra="forbid")

    artifact_name: str | None = None
    artifact_kind: str
    session_id: str | None = None
    task_id: str
    node_id: str
    attempt: int
    text_content: dict[str, Any] | None = None
    file_content: dict[str, Any] | None = None
    url_link_content: dict[str, Any] | None = None
    code_content: dict[str, Any] | None = None


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
    """单条 trace step——七字段**全键必填**(2026-10-15 契约):

    ``action_type|action_input|action_result|status_from|status_to|boost_reason|
    error_msg`` 一键不可缺(值可为 None)。``action_type`` 即 Bot 侧 step 码
    (S 后带数字+其它,如 S1_CONTEXT)——2026-10-15 起其即轨迹主事件的
    action_type,读侧按 ``^S\\d+`` 识别主序列,不再限定枚举词表。

    2026-10-16 语义复核:``action_input`` 是步骤输入;``action_result`` 是步骤
    推理输出/产出摘要,必须为可解析的 JSON object 字符串,且包含非空 ``status``
    字段与至少一个输出字段;``completed/failed/ready/not_applicable`` 等状态值
    只能作为 JSON 内的 ``status`` 字段。``boost_reason`` 是执行过程证据：为什么推进、为什么选择等等维度信息;``error_msg`` 是失败原因;``status_from/status_to`` 是节点状态。
    """

    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(min_length=1)
    action_input: str | None
    action_result: str = Field(min_length=1)
    status_from: str | None
    status_to: str | None
    boost_reason: str | None
    error_msg: str | None

    @field_validator(
        "action_type",
        "action_input",
        "action_result",
        "status_from",
        "status_to",
        "boost_reason",
        "error_msg",
        mode="after",
    )
    @classmethod
    def _reject_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must be a non-empty string when present")
        return value

    @field_validator("action_result", mode="after")
    @classmethod
    def _require_step_output_json(cls, value: str) -> str:
        try:
            output = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "action_result must be a JSON object containing the step output"
            ) from exc
        if not isinstance(output, dict):
            raise ValueError(
                "action_result must be a JSON object containing the step output"
            )
        status = output.get("status")
        if not isinstance(status, str) or not status.strip():
            raise ValueError("action_result.status must be a non-empty string")
        if len(output) <= 1:
            raise ValueError(
                "action_result must contain step output fields in addition to status"
            )
        return value


class TaskReportTraceDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[TaskReportTraceStepDTO]


_TRACE_ACTION_TYPES: dict[TaskReportEventType, tuple[str, ...]] = {
    TaskReportEventType.EXECUTION_RESULT: (
        "S1_CONTEXT",
        "S2_GAPS",
        "S3_CAPABILITY",
        "S4A_EXECUTION",
        "S4B_REPORT",
    ),
    TaskReportEventType.PLAN_RESULT: (
        "S5A_REFRESH_GAPS",
        "S5B_PLAN_NEXT_NODE",
        "S5C_SEARCH_CANDIDATES",
        "S5D_SELECT_EXECUTOR",
        "S5E_REPORT_PLAN",
    ),
}


def _step_contains_relay_token(step: TaskReportTraceStepDTO, relay_token: str) -> bool:
    values = (
        step.action_type,
        step.action_input,
        step.action_result,
        step.status_from,
        step.status_to,
        step.boost_reason,
        step.error_msg,
    )
    return any(
        isinstance(value, str)
        and ("relay_token" in value or relay_token in value)
        for value in values
    )


class TaskReportCommandDTO(BaseModel):
    """Two-event Relay report command; ``trace`` is optional on the wire.

    When ``trace`` is provided, ``trace.steps`` must be present, every step
    must carry the seven fixed fields, and the step sequence must match the
    report event type.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    relay_token: str = Field(min_length=1)
    event_type: TaskReportEventType
    event: dict[str, Any] | None = None
    # 产物上报(2026-10-22 用户定稿):报文顶层可选列表,与 trace/task_acceptance_result
    # 平级——产物是"一次执行的结果清单"而非事件内部细节。携带条件:仅
    # EXECUTION_RESULT 且 execution_decision=ACCEPTED(to_domain 拒绝其余一切组合)。
    artifacts: list[ArtifactReportItemDTO] | None = None
    progress_reason: str = ""
    failure_reason: str | None = None
    task_acceptance_result: AcceptanceResultDTO | None = None
    trace: TaskReportTraceDTO | None = None

    @field_validator("task_id", "node_id", "event_id", "relay_token", mode="after")
    @classmethod
    def _reject_blank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value

    @model_validator(mode="after")
    def _validate_trace_contract(self) -> "TaskReportCommandDTO":
        if self.trace is None:
            return self
        expected = _TRACE_ACTION_TYPES[self.event_type]
        actual = tuple(step.action_type for step in self.trace.steps)
        if actual != expected:
            raise ValueError(
                "trace.steps must cover action types in order: "
                + ", ".join(expected)
            )
        if any(
            _step_contains_relay_token(step, self.relay_token)
            for step in self.trace.steps
        ):
            raise ValueError("trace step must not contain relay_token")
        return self

    def to_domain(self) -> TaskReportCommand:
        # 产物携带条件(报文顶层 artifacts,2026-10-22 用户定稿):仅 EXECUTION_RESULT
        # 事件允许携带;其余事件类型(如 PLANNED)夹带即整体拒绝。
        if self.artifacts is not None and self.event_type != TaskReportEventType.EXECUTION_RESULT:
            raise TaskStateError(
                f"{self.event_type.value} events must not carry artifacts"
            )
        if self.event_type == TaskReportEventType.EXECUTION_RESULT:
            if self.event is None:
                raise TaskStateError("EXECUTION_RESULT requires an event")
            parsed = ExecutionEventDTO.model_validate(self.event)
            if parsed.task_id is not None and parsed.task_id != self.task_id:
                raise TaskStateError("execution patch belongs to another task")
            if parsed.node_id is not None and parsed.node_id != self.node_id:
                raise TaskStateError("execution patch targets another node")
            if self.artifacts is not None and str(parsed.execution_decision).upper() != "ACCEPTED":
                # 只有 ACCEPTED 事件允许携带产物;DECLINED 夹带即整体拒绝,不静默丢弃。
                raise TaskStateError("declined execution events must not carry artifacts")
            artifact_patch = (
                parse_artifact_report_items(
                    [item.model_dump() for item in self.artifacts],
                    report_task_id=self.task_id,
                    report_node_id=self.node_id,
                )
                if self.artifacts is not None
                else None
            )
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
                artifact_patch=artifact_patch,
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
