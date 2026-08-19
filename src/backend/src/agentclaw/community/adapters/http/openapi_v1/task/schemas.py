"""Task HTTP API schemas —— FastAPI 边界 DTO <-> core task domain models。

纯 pydantic.BaseModel 序列化,不含业务逻辑(Rule 22:HTTP adapter 只转协议)。
对齐 domain/models.py 的 TaskInfo/TaskCallbackData/TaskExecutionGraph/TaskOpResult/TaskNode 字段。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Success/error envelopes come from the unified /openapi/v1 contract
# (``openapi_v1.contracts.Envelope`` / ``ErrorEnvelope``); this module keeps only
# the task-specific request/response DTOs.


# ===== Request DTOs =====


class MetadataDTO(BaseModel):
    task_id: str = Field(..., description="任务ID")
    title: str = Field("", description="任务标题")
    instruction: str = Field("", description="核心执行指令(Prompt)")


class ContextDTO(BaseModel):
    background: str = Field("", description="任务背景信息")
    extend_props: dict[str, Any] = Field(default_factory=dict, description="上下文扩展属性")


class AcceptanceCriteriaDTO(BaseModel):
    id: str = Field(..., description="验收标准唯一标识")
    description: str = Field("", description="验收标准具体描述")


class GoalDTO(BaseModel):
    objective: str = Field("", description="任务目标描述")
    acceptances: list[AcceptanceCriteriaDTO] = Field(default_factory=list, description="验收标准列表")


class TaskSpecDTO(BaseModel):
    metadata: MetadataDTO
    context: ContextDTO = Field(default_factory=ContextDTO)
    goal: GoalDTO = Field(default_factory=GoalDTO)


class TaskInfoDTO(BaseModel):
    """POST /openapi/v1/task/execute 请求体。"""
    task_spec: TaskSpecDTO
    source_channel_type: str = Field("bot", description="任务来源渠道: bot / coop_group")
    source_channel_id: str = Field(..., description="来源ID: bot_id / 协作群id")
    execution_config: dict[str, Any] = Field(default_factory=dict, description="执行配置(MAX_DEPTH/MAX_LOOP/MAX_HARNESS/bot/workflow 等)")


class BbsClaimDTO(BaseModel):
    """POST /openapi/v1/task/bbs/claim 请求体。"""

    task_id: str = Field(..., description="任务ID(BBS 接力根级 CAS 占有目标)")
    bot_id: str = Field(..., description="发起占有的 bot id")


class BbsAttachDTO(BaseModel):
    """POST /openapi/v1/task/bbs/attach 请求体(BBS 接力步④:挂 scoped bbs 子节点 + start)。"""

    task_id: str = Field(..., description="任务ID")
    parent_node_id: str = Field(..., description="父节点ID(挂入分解树的 parent)")
    task_spec: TaskSpecDTO = Field(..., description="scoped 子节点任务规格")
    bot_id: str = Field(..., description="发起挂接的 bot id(须为当前 bbs_owner)")


class BbsResultDTO(BaseModel):
    """POST /openapi/v1/task/bbs/result 请求体(BBS 接力步⑤:回投 scoped 节点终态 + 释放 claim)。

    收口不由 bot 声明:框架经 owner 复核根 gap 满足后自行收口(``on_bbs_report``→``_on_pass_collect``→
    ``_maybe_finish_graph``),故无 ``root_verified`` 字段。
    """

    task_id: str = Field(..., description="任务ID")
    node_id: str = Field(..., description="scoped 子节点ID(attach 返回的 bbs- 节点)")
    bot_id: str = Field(..., description="回投 bot id(须为当前 bbs_owner)")
    acceptance_result: AcceptanceResultDTO | None = Field(None, description="验收结论(PASS/FAIL)")
    output_patch: dict[str, Any] | None = Field(None, description="checkpoint fold 增量输出")
    exec_error: str | None = Field(None, description="执行报错(fold 进节点)")


class TaskCallbackDataDTO(BaseModel):
    """POST /openapi/v1/task/callback/report 请求体(执行实体回投)。"""
    loop_task_id: str = Field(..., description="回投标识 f'{task_id}::{node_id}'")
    workflow_type: str = Field("single_bot", description="执行模态 single_bot / bcn_coop_group")
    workflow_id: int = Field(0, description="workflow id(占位)")
    instance_id: int = Field(0, description="instance id(占位)")
    result: dict[str, Any] = Field(
        default_factory=dict,
        description="回投结果 {success: bool, data?: any, fail_detail?: str}",
    )


# ===== Response DTOs =====


class TaskOpResultDTO(BaseModel):
    task_id: str
    success: bool
    run_id: int = Field(0, description="图运行实例ID")
    message: str | None = None


class AcceptanceResultDTO(BaseModel):
    verdict: str = Field(..., description="PASS / FAIL")
    acceptances_metric: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class NodeActionEventDTO(BaseModel):
    """节点动作级历史快照(诊断用;默认不序列化,include_action_log=true 时返回)。"""
    seq: int
    ts: int
    action: str
    loop_round: int = 0
    attempt: int = 0
    status_from: str | None = None
    status_to: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RuntimeInfoDTO(BaseModel):
    run_mode: str | None = None
    assignee: str | None = None
    start_time: int | None = None
    end_time: int | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    acceptance_result: AcceptanceResultDTO | None = None
    extend_props: dict[str, Any] = Field(default_factory=dict)
    action_log: list[NodeActionEventDTO] = Field(default_factory=list)


class TaskNodeDTO(BaseModel):
    node_id: str
    task_id: str
    status: str
    task_spec: TaskSpecDTO
    run_info: RuntimeInfoDTO = Field(default_factory=RuntimeInfoDTO)


class TaskSummaryDTO(BaseModel):
    """GET /openapi/v1/task/list 返回项(轻量投影)。"""
    task_id: str
    run_id: int
    status: str
    title: str = ""
    node_count: int = 0
    loop_round: int = 0
    bbs_mode: bool = False


class TaskExecutionGraphDTO(BaseModel):
    run_id: int
    loop_round: int
    status: str
    output: dict[str, Any] = Field(default_factory=dict)
    tasks: list[TaskNodeDTO] = Field(default_factory=list)
    extend_props: dict[str, Any] = Field(default_factory=dict)


# ===== DTO <-> domain conversion(Rule 22:adapter 唯一写/读翻译位) =====


def task_spec_from_dto(dto: TaskSpecDTO):
    """TaskSpecDTO → domain TaskSpec(Rule 22:adapter 唯一写翻译位;task_info_from_dto / bbs_attach 复用)。"""
    from agentclaw.community.core.task.domain.models import (
        AcceptanceCriteria, Context, Goal, Metadata, TaskSpec,
    )
    return TaskSpec(
        metadata=Metadata(task_id=dto.metadata.task_id,
                          title=dto.metadata.title,
                          instruction=dto.metadata.instruction),
        context=Context(background=dto.context.background,
                        extend_props=dict(dto.context.extend_props)),
        goal=Goal(objective=dto.goal.objective,
                  acceptances=[AcceptanceCriteria(id=a.id, description=a.description)
                               for a in dto.goal.acceptances]),
    )


def task_info_from_dto(dto: TaskInfoDTO):
    from agentclaw.community.core.task.domain.models import TaskInfo
    return TaskInfo(
        task_spec=task_spec_from_dto(dto.task_spec),
        source_channel_type=dto.source_channel_type,
        source_channel_id=dto.source_channel_id,
        execution_config=dict(dto.execution_config),
    )


def callback_from_dto(dto: TaskCallbackDataDTO):
    from agentclaw.community.core.task.domain.models import TaskCallbackData
    return TaskCallbackData(
        loop_task_id=dto.loop_task_id,
        workflow_type=dto.workflow_type,
        workflow_id=dto.workflow_id,
        instance_id=dto.instance_id,
        result=dict(dto.result),
    )


def acceptance_result_from_dto(dto: AcceptanceResultDTO):
    """AcceptanceResultDTO → domain AcceptanceResult(Rule 22:adapter 唯一写翻译位;bbs/result 路由复用)。"""
    from agentclaw.community.core.task.domain.models import AcceptanceResult, AcceptanceVerdict
    return AcceptanceResult(
        verdict=AcceptanceVerdict(dto.verdict),
        acceptances_metric=list(dto.acceptances_metric),
        gaps=list(dto.gaps),
    )


def graph_to_dto(graph, *, include_action_log: bool = False) -> TaskExecutionGraphDTO:
    nodes: list[TaskNodeDTO] = []
    for n in graph.tasks:
        ar = n.run_info.acceptance_result
        ar_dto = (AcceptanceResultDTO(verdict=ar.verdict.value,
                                      acceptances_metric=list(ar.acceptances_metric),
                                      gaps=list(ar.gaps))
                  if ar is not None else None)
        nodes.append(TaskNodeDTO(
            node_id=n.node_id, task_id=n.task_id, status=n.status.value,
            task_spec=TaskSpecDTO(
                metadata=MetadataDTO(task_id=n.task_spec.metadata.task_id,
                                     title=n.task_spec.metadata.title,
                                     instruction=n.task_spec.metadata.instruction),
                context=ContextDTO(background=n.task_spec.context.background,
                                   extend_props=dict(n.task_spec.context.extend_props)),
                goal=GoalDTO(objective=n.task_spec.goal.objective,
                             acceptances=[AcceptanceCriteriaDTO(id=a.id, description=a.description)
                                          for a in n.task_spec.goal.acceptances]),
            ),
            run_info=RuntimeInfoDTO(run_mode=n.run_info.run_mode,
                                    assignee=n.run_info.assignee,
                                    start_time=n.run_info.start_time,
                                    end_time=n.run_info.end_time,
                                    output=dict(n.run_info.output),
                                    acceptance_result=ar_dto,
                                    extend_props=dict(n.run_info.extend_props),
                                    action_log=([NodeActionEventDTO(
                                        seq=e.seq, ts=e.ts, action=e.action.value,
                                        loop_round=e.loop_round, attempt=e.attempt,
                                        status_from=e.status_from.value if e.status_from else None,
                                        status_to=e.status_to.value if e.status_to else None,
                                        payload=dict(e.payload),
                                    ) for e in n.run_info.action_log]
                                    if include_action_log else [])),
        ))
    return TaskExecutionGraphDTO(
        run_id=graph.run_id, loop_round=graph.loop_round, status=graph.status.value,
        output=dict(graph.output), tasks=nodes, extend_props=dict(graph.extend_props),
    )



def summary_to_dto(s) -> TaskSummaryDTO:
    """TaskSummary -> TaskSummaryDTO(Rule 22)。"""
    return TaskSummaryDTO(task_id=s.task_id, run_id=s.run_id, status=s.status.value,
                          title=s.title, node_count=s.node_count, loop_round=s.loop_round,
                          bbs_mode=s.bbs_mode)

def op_result_to_dto(result) -> TaskOpResultDTO:
    return TaskOpResultDTO(task_id=result.task_id, success=result.success, run_id=result.run_id,
                           message=getattr(result, "message", None))


# ===== task_loop inbound callback schemas(PUSH 回调,对齐羽雀 TaskCallbackData/TaskNodeCallbackData)=====
# SSOT TaskCallbackData 保持精简(不扩);羽雀丰富字段在 translator 边缘折叠进 SSOT。
# 必填非可选(AGENTS.md):task_id/workflow_source/workflow_id/workflow_instance_id/status/is_success。
# None 仅契约态:goal/output/failed_info/ext_info/loop_task_id(回声字段,缺失走 registry)。


class TaskCallbackRequest(BaseModel):
    """task 级(workflow)回调载荷。"""

    task_id: str
    workflow_source: Literal["claw_mind", "bcn"]
    workflow_id: str
    workflow_instance_id: str
    goal: str | None = None
    status: str
    is_success: bool
    output: dict[str, Any] | None = None
    failed_info: str | None = None
    ext_info: dict[str, Any] | None = None
    loop_task_id: str | None = None    # 回声字段:派发期透传,引擎原样回带(可选)


class TaskNodeCallbackRequest(TaskCallbackRequest):
    """node 级回调载荷(node_id 即 Avernet 子节点 id,统一领域对象 1:1 映射)。"""

    node_id: str
