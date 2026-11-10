"""HTTP DTOs for the context collab delivery (spec §6.2-§6.5).

Wire models are the §6.5 OpenAPI schema names verbatim — ``ColletCreateRequest``,
``EventCreateRequest``, ``ContextCollabStatusResponse``, ``TaskProgress``,
``ContextSpec`` and the L2/L3 reference DTOs. Request models carry
``to_domain()`` builders for the service commands (the intake
``TaskIntakeCommandDTO.to_domain`` pattern); response models mirror exactly
what ``ContextCollabService`` projects, including the ``occurred_at`` field
the GET ``current_event`` projection carries beyond the 7-field set.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ContextCollabCreatedResult,
    ContextCollabStatusProjection,
    ContextCreateCommand,
    EventAcceptedResult,
    EventAppendCommand,
)


# ── L2/L3 引用 DTO(spec §6.2 / §6.3 请求中的引用对象)────────────────────


class PrincipalRefDto(BaseModel):
    """L2 责任主体引用 wire 形态;``to_domain`` 复用 value object 校验。"""

    model_config = ConfigDict(extra="forbid")

    principal_type: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)

    def to_domain(self) -> PrincipalRef:
        return PrincipalRef.from_dict(self.model_dump())


class AgentRefDto(BaseModel):
    """L2 执行 Agent 引用 wire 形态。"""

    model_config = ConfigDict(extra="forbid")

    agent_type: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)

    def to_domain(self) -> AgentRef:
        return AgentRef.from_dict(self.model_dump())


class SpaceRefDto(BaseModel):
    """L3 Space 引用 wire 形态。"""

    model_config = ConfigDict(extra="forbid")

    space_id: str = Field(min_length=1)
    environment: str = Field(min_length=1)

    def to_domain(self) -> SpaceRef:
        return SpaceRef.from_dict(self.model_dump())


class ResourceRefDto(BaseModel):
    """L3 外部资源引用 wire 形态;``revision`` 可空(无版本要求时)。"""

    model_config = ConfigDict(extra="forbid")

    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    revision: str | None = None

    def to_domain(self) -> ResourceRef:
        return ResourceRef.from_dict(self.model_dump())


# ── 请求 DTO(spec §6.2 表 / §6.3 表,字段一一对应)─────────────────────────


class ContextSpec(BaseModel):
    """ContextSpec 形态的嵌套 DTO(spec §6.5 schema 名)。

    与 §6.2 请求表中除 ``request_id`` 外的字段一一对应;L2/L3/L5/L6 信息
    以引用或枚举字符串保存,不复制外部注册表。
    """

    model_config = ConfigDict(extra="forbid")

    space: SpaceRefDto
    principal_ref: PrincipalRefDto
    product_kind: str | None = None
    workflow_kind: str | None = None
    method_kind: str = Field(min_length=1)
    executor_ref: AgentRefDto | None = None
    objective: str = Field(min_length=1)
    resource_refs: list[ResourceRefDto] = Field(default_factory=list)


class ColletCreateRequest(ContextSpec):
    """spec §6.2 新增协作上下文请求 = ContextSpec 字段 + ``request_id`` 幂等键。"""

    model_config = ConfigDict(extra="ignore")

    request_id: str = Field(min_length=1)

    def to_domain(self) -> ContextCreateCommand:
        return ContextCreateCommand(
            request_id=self.request_id,
            space=self.space.to_domain(),
            principal_ref=self.principal_ref.to_domain(),
            product_kind=self.product_kind,
            workflow_kind=self.workflow_kind,
            method_kind=self.method_kind,
            executor_ref=(
                self.executor_ref.to_domain() if self.executor_ref else None
            ),
            objective=self.objective,
            resource_refs=[ref.to_domain() for ref in self.resource_refs],
        )


class EventCreateRequest(BaseModel):
    """spec §6.3 追加事件请求;字段与请求表一一对应。

    ``actor_ref`` 与认证主体的对账规则(G1:不接受伪造 actor — delivery
    层裁决):省略/为空时取已认证 principal;显式给出时其
    ``principal_id`` 必须等于已认证 ``staffId``,否则
    ``ContextCollabValidationError``(422),发生在调用服务之前。
    """

    model_config = ConfigDict(extra="ignore")

    request_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    event_kind: str = Field(min_length=1)
    primitive_type: str = Field(min_length=1)
    actor_ref: PrincipalRefDto | None = None
    parent_event: str | None = None
    visibility: str = Field(min_length=1)
    payload: dict = Field(default_factory=dict)

    def to_domain(self, authenticated_actor: PrincipalRef) -> EventAppendCommand:
        actor_ref = (
            self.actor_ref.to_domain() if self.actor_ref is not None
            else authenticated_actor
        )
        if actor_ref.principal_id != authenticated_actor.principal_id:
            raise ContextCollabValidationError(
                "actor_ref.principal_id must match the authenticated principal"
            )
        return EventAppendCommand(
            request_id=self.request_id,
            expected_version=self.expected_version,
            event_kind=self.event_kind,
            primitive_type=self.primitive_type,
            actor_ref=actor_ref,
            parent_event=self.parent_event,
            visibility=self.visibility,
            payload=self.payload,
        )


# ── 响应 DTO(spec §6.2 / §6.3 / §6.4 响应示例)────────────────────────────


class ProgressDto(BaseModel):
    """§6.4 ``current_snapshot.progress`` 投影。"""

    percent: int
    completed_count: int = 0
    active_count: int = 0
    blocked_count: int = 0
    current_event_id: str | None = None


class CurrentSnapshotDto(BaseModel):
    """§6.2/§6.4 响应中 ``current_snapshot`` 的形态。

    ``principal_ref`` / ``executor_ref`` 在 wire 上是裸 ID 字符串
    (响应示例即如此);服务两条路径投影形态不同(创建侧已是裸 ID,
    查询侧 ``CurrentSnapshot.to_dict`` 返回引用 dict),由
    ``_snapshot_to_dto`` 统一归一。
    """

    objective: str
    status: str
    principal_ref: str
    executor_ref: str | None = None
    method_kind: str
    progress: ProgressDto


class ContextEventBriefDto(BaseModel):
    """§6.2 响应 ``current_event`` 的基础四字段投影。"""

    event_id: str
    parent_event: str | None = None
    event_kind: str
    primitive_type: str


class ContextCollabCreatedResultDto(BaseModel):
    """§6.2 创建响应 + 服务幂等重放标记。"""

    collab_context_id: str
    context_version: int
    current_snapshot: CurrentSnapshotDto
    current_event: ContextEventBriefDto
    replayed: bool


class EventAcceptedResultDto(BaseModel):
    """§6.3 追加事件响应(8 字段)。"""

    accepted: bool
    collab_context_id: str
    event_id: str
    parent_event: str | None
    event_kind: str
    primitive_type: str
    context_version: int
    current_status: str


class TaskProgressEventDto(BaseModel):
    """§6.4 ``task_progress.events[]`` 条目(7 字段投影,无 ``occurred_at``)。"""

    event_id: str
    parent_event: str | None = None
    event_kind: str
    primitive_type: str
    status: str
    summary: str | None = None
    content: str | None = None


class CurrentEventDto(TaskProgressEventDto):
    """§6.4 ``current_event``:进度条目 + ``occurred_at``(服务返回的第 8 字段,

    不能在 DTO 层丢掉)。"""

    occurred_at: datetime | None = None


class TaskProgress(BaseModel):
    """spec §6.5 schema 名 ``TaskProgress``(§6.4 任务进度块)。"""

    total_events: int
    events: list[TaskProgressEventDto]


class ContextCollabStatusResponse(BaseModel):
    """spec §6.5 schema 名 ``ContextCollabStatusResponse``(§6.4 响应)。"""

    collab_context_id: str
    context_version: int
    current_snapshot: CurrentSnapshotDto
    current_event: CurrentEventDto | None
    task_progress: TaskProgress


# ── *_to_dto 转换 ───────────────────────────────────────────────────────────


def _principal_id(value: object) -> str | None:
    """归一 ``principal_ref`` / ``executor_ref`` 服务的两种投影形态。

    创建路径返回裸 ID 字符串;GET 路径的 ``CurrentSnapshot.to_dict`` 返回
    引用 dict(spec §6.4 响应示例在 wire 上始终是裸 ID)。
    """
    if value is None:
        return None
    if isinstance(value, dict):
        principal_id = value.get("principal_id")
        if principal_id is not None:
            return str(principal_id)
        agent_id = value.get("agent_id")
        return str(agent_id) if agent_id is not None else None
    return str(value)


def _snapshot_to_dto(snapshot: dict) -> CurrentSnapshotDto:
    progress = snapshot["progress"]
    return CurrentSnapshotDto(
        objective=snapshot["objective"],
        status=snapshot["status"],
        principal_ref=_principal_id(snapshot["principal_ref"]),
        executor_ref=_principal_id(snapshot["executor_ref"]),
        method_kind=snapshot["method_kind"],
        progress=ProgressDto(**progress),
    )


def created_result_to_dto(result: ContextCollabCreatedResult) -> ContextCollabCreatedResultDto:
    event = result.current_event
    return ContextCollabCreatedResultDto(
        collab_context_id=result.collab_context_id,
        context_version=result.context_version,
        current_snapshot=_snapshot_to_dto(result.current_snapshot),
        current_event=ContextEventBriefDto(
            event_id=event["event_id"],
            parent_event=event["parent_event"],
            event_kind=event["event_kind"],
            primitive_type=event["primitive_type"],
        ),
        replayed=result.replayed,
    )


def event_accepted_to_dto(result: EventAcceptedResult) -> EventAcceptedResultDto:
    return EventAcceptedResultDto(
        accepted=result.accepted,
        collab_context_id=result.collab_context_id,
        event_id=result.event_id,
        parent_event=result.parent_event,
        event_kind=result.event_kind,
        primitive_type=result.primitive_type,
        context_version=result.context_version,
        current_status=result.current_status,
    )


def status_to_dto(result: ContextCollabStatusProjection) -> ContextCollabStatusResponse:
    current_event = result.current_event
    return ContextCollabStatusResponse(
        collab_context_id=result.collab_context_id,
        context_version=result.context_version,
        current_snapshot=_snapshot_to_dto(result.current_snapshot),
        current_event=(
            CurrentEventDto(
                event_id=current_event["event_id"],
                parent_event=current_event["parent_event"],
                event_kind=current_event["event_kind"],
                primitive_type=current_event["primitive_type"],
                status=current_event["status"],
                summary=current_event["summary"],
                content=current_event["content"],
                occurred_at=current_event["occurred_at"],
            )
            if current_event is not None
            else None
        ),
        task_progress=TaskProgress(
            total_events=result.task_progress.total_events,
            events=[
                TaskProgressEventDto(
                    event_id=event["event_id"],
                    parent_event=event["parent_event"],
                    event_kind=event["event_kind"],
                    primitive_type=event["primitive_type"],
                    status=event["status"],
                    summary=event["summary"],
                    content=event["content"],
                )
                for event in result.task_progress.events
            ],
        ),
    )
