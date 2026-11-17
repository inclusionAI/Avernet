"""HTTP DTOs for the context collab delivery (spec §6.2-§6.5).

Wire models are the §6.5 OpenAPI schema names verbatim — ColletCreateRequest,
EventCreateRequest, ContextCollabStatusResponse, TaskProgress,
the creation request and the L2/L3 reference DTOs. Request models carry
to_domain() builders for the service commands (the intake
TaskIntakeCommandDTO.to_domain pattern). The creation receipt combines the
returned Collet with the accepted command and immutable creation Event; other responses serialize service
projections, including the occurred_at field
the GET current_event projection carries beyond the 7-field set.

Docstrings and Field descriptions are published verbatim into the public
OpenAPI document (the /openapi/v1 twin mounts these models), so they stay
caller documentation: no internal route paths, no RST markup.
"""

from __future__ import annotations

from datetime import datetime

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
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.service_api import (
    CollaborationCreateCommand,
    ContextCollabStatusProjection,
    EventAcceptedResult,
    EventAppendCommand,
)
from pydantic import BaseModel, ConfigDict, Field, model_serializer

# ── L2/L3 引用 DTO(spec §6.2 / §6.3 请求中的引用对象)────────────────────


class PrincipalRefDto(BaseModel):
    """L2 accountable-principal reference; validated by the shared value object."""

    model_config = ConfigDict(extra="forbid")

    principal_type: str = Field(
        min_length=1,
        description="Accountable principal type: human, bot, org, or system.",
    )
    principal_id: str = Field(
        min_length=1,
        description="External identifier of the accountable principal, as "
        "issued by the identity system of record.",
    )

    def to_domain(self) -> PrincipalRef:
        return PrincipalRef.from_dict(self.model_dump())


class AgentRefDto(BaseModel):
    """L2 executing-agent reference."""

    model_config = ConfigDict(extra="forbid")

    agent_type: str = Field(
        min_length=1,
        description="Executing agent type: bot or service.",
    )
    agent_id: str = Field(
        min_length=1,
        description="External identifier of the executing agent.",
    )

    def to_domain(self) -> AgentRef:
        return AgentRef.from_dict(self.model_dump())


class SpaceRefDto(BaseModel):
    """L3 space reference."""

    model_config = ConfigDict(extra="forbid")

    space_id: str = Field(
        min_length=1,
        description="L3 space identifier this collaboration runs in.",
    )
    environment: str = Field(
        min_length=1,
        description="Environment the space reference is valid in (e.g. prod).",
    )

    def to_domain(self) -> SpaceRef:
        return SpaceRef.from_dict(self.model_dump())


class ResourceRefDto(BaseModel):
    """L3 external-resource reference; revision optional when unversioned."""

    model_config = ConfigDict(extra="forbid")

    resource_type: str = Field(
        min_length=1,
        description="External resource type (e.g. forum_topic).",
    )
    resource_id: str = Field(
        min_length=1,
        description="External identifier of the referenced resource.",
    )
    revision: str | None = Field(
        default=None,
        description="Optional revision of the referenced resource; omit when "
        "the collaboration does not require a specific version.",
    )

    def to_domain(self) -> ResourceRef:
        return ResourceRef.from_dict(self.model_dump())


# ── 请求 DTO(spec §6.2 表 / §6.3 表,字段一一对应)─────────────────────────


class ColletCreateRequest(BaseModel):
    """Flat creation request published as the collaboration request schema (spec §6.5).

    Fields map one-to-one onto the §6.2 create-request table; L2/L3/L5/L6
    information is held as references or enum strings rather than copied
    from the external registries. Creation carries no idempotency key:
    a repeated call creates a second context.
    """

    model_config = ConfigDict(extra="forbid")

    space: SpaceRefDto = Field(description="L3 space the collaboration runs in.")
    principal_ref: PrincipalRefDto = Field(
        description="L2 accountable principal owning the collaboration context."
    )
    product_kind: str | None = Field(
        default=None,
        description="Optional L6 product kind, e.g. team_todo, "
        "public_discussion, bbs_public_discussion, or bbs_help_request.",
    )
    workflow_kind: str | None = Field(
        default=None,
        description="Optional L6 workflow kind, e.g. team_todo_execution, "
        "bbs_public_discussion, or bbs_help_request_goal; stored as a plain "
        "string, no profile table is created.",
    )
    method_kind: str = Field(
        min_length=1,
        description="L5 collaboration method: Single or Open+Group.",
    )
    executor_ref: AgentRefDto | None = Field(
        default=None,
        description="Required when method_kind is Single: the L2 agent "
        "executing the collaboration.",
    )
    objective: str = Field(
        min_length=1,
        description="Current collaboration objective.",
    )
    resource_refs: list[ResourceRefDto] = Field(
        default_factory=list,
        description="Optional L3 external-resource references; resource "
        "content is never copied.",
    )
    product_profile_id: str | None = Field(
        default=None,
        description="Optional exact L6 product profile identifier.",
    )
    product_profile_version: int | None = Field(
        default=None,
        ge=1,
        description="Optional exact L6 product profile version.",
    )

    def to_domain(self) -> CollaborationCreateCommand:
        return CollaborationCreateCommand(
            to_space=self.space.to_domain(),
            principal_ref=self.principal_ref.to_domain(),
            product_kind=self.product_kind,
            workflow_kind=self.workflow_kind,
            method_kind=self.method_kind,
            executor_ref=(self.executor_ref.to_domain() if self.executor_ref else None),
            objective=self.objective,
            resource_refs=[ref.to_domain() for ref in self.resource_refs],
            product_profile_ref=(
                VersionedProfileRef(
                    self.product_profile_id, self.product_profile_version
                )
                if self.product_profile_id is not None
                and self.product_profile_version is not None
                else None
            ),
        )


class EventCreateRequest(BaseModel):
    """Append-event request; fields map one-to-one onto the §6.3 request table.

    actor_ref is reconciled against the authenticated principal at delivery
    (G1: a forged actor is not accepted): omitted means the authenticated
    principal, and when supplied its principal_id must equal the verified
    caller's, otherwise the request fails validation with 422 before the
    service is invoked.
    """

    model_config = ConfigDict(extra="ignore")

    expected_version: int = Field(
        ge=1,
        description="Context version the append is based on; a mismatch with "
        "the current version is rejected with a version-conflict error.",
    )
    event_kind: str = Field(
        min_length=1,
        description="Registry kind of the event, e.g. task.created or "
        "user_interaction.posted.",
    )
    primitive_type: str = Field(
        min_length=1,
        description="L4 primitive the event carries: Task, Message, or Activity.",
    )
    actor_ref: PrincipalRefDto | None = Field(
        default=None,
        description="Optional accountable principal to attribute the event "
        "to; defaults to the authenticated caller and must match the "
        "verified principal id when supplied.",
    )
    parent_event: str | None = Field(
        default=None,
        description="Optional parent event id; events without one extend the "
        "trunk chain directly.",
    )
    visibility: str = Field(
        min_length=1,
        description="Visibility scope of the event, e.g. workspace.",
    )
    payload: dict = Field(
        default_factory=dict,
        description="Event payload, validated against the event-kind schema registry.",
    )

    def to_domain(self, authenticated_actor: PrincipalRef) -> EventAppendCommand:
        actor_ref = (
            self.actor_ref.to_domain()
            if self.actor_ref is not None
            else authenticated_actor
        )
        if actor_ref.principal_id != authenticated_actor.principal_id:
            raise ContextCollabValidationError(
                "actor_ref.principal_id must match the authenticated principal"
            )
        return EventAppendCommand(
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
    """Progress projection carried on each current_snapshot."""

    percent: int = Field(description="Completion percentage 0-100.")
    completed_count: int = Field(
        default=0, description="Number of completed events in the snapshot."
    )
    active_count: int = Field(
        default=0, description="Number of active (in-flight) events."
    )
    blocked_count: int = Field(
        default=0, description="Number of blocked events in the snapshot."
    )
    current_event_id: str | None = Field(
        default=None,
        description="Event id the current snapshot is anchored on, if any.",
    )


class CurrentSnapshotDto(BaseModel):
    """Shape of current_snapshot in the §6.2 create and §6.4 status responses.

    principal_ref and executor_ref are bare id strings on the wire (both
    service projections are normalized by _snapshot_to_dto before
    serialization).
    """

    objective: str = Field(description="Current collaboration objective.")
    status: str = Field(description="Current snapshot status, e.g. created.")
    principal_ref: str = Field(description="Bare id of the L2 accountable principal.")
    executor_ref: str | None = Field(
        default=None,
        description="Bare id of the executing agent, when one is bound.",
    )
    executor_refs: list[str] | None = Field(
        default=None,
        description="All executor ids for multiple-executor Work; absent for legacy single/unassigned views.",
    )
    method_kind: str | None = Field(
        description="Selected L5 method; null before binding."
    )
    progress: ProgressDto = Field(description="Progress counters.")

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        data = handler(self)
        if self.executor_refs is None:
            data.pop("executor_refs", None)
        return data


class ContextEventBriefDto(BaseModel):
    """Four-field brief of the §6.2 create-response current_event."""

    event_id: str = Field(description="Immutable id of the event.")
    parent_event: str | None = Field(
        default=None, description="Parent event id, when the event has one."
    )
    event_kind: str = Field(description="Registry kind of the event.")
    primitive_type: str = Field(description="L4 primitive the event carries.")


class ContextCollabCreatedResultDto(BaseModel):
    """Create response(spec §6.2)。"""

    collab_context_id: str = Field(
        description="Id of the created collaboration context."
    )
    context_version: int = Field(description="Context version after creation (1).")
    current_snapshot: CurrentSnapshotDto = Field(
        description="Snapshot anchored on the initial task.created event."
    )
    current_event: ContextEventBriefDto = Field(
        description="The initial task.created event."
    )


class EventAcceptedResultDto(BaseModel):
    """Append-event acceptance response (8 fields)."""

    accepted: bool = Field(
        description="True when the event was accepted onto the context."
    )
    collab_context_id: str = Field(
        description="Id of the collaboration context the event joined."
    )
    event_id: str = Field(description="Immutable id of the accepted event.")
    parent_event: str | None = Field(
        description="Parent event id the event was linked to, when one was given."
    )
    event_kind: str = Field(description="Registry kind of the event.")
    primitive_type: str | None = Field(
        description="L4 primitive the event carries; null for an "
        "out-of-band partial event row."
    )
    context_version: int = Field(
        description="Context version after the accepted append."
    )
    current_status: str | None = Field(
        description="Snapshot status after the append; null when the "
        "aggregate row was written out-of-band without a snapshot."
    )


class TaskProgressEventDto(BaseModel):
    """One task_progress.events entry (7-field projection, no occurred_at)."""

    event_id: str = Field(description="Immutable id of the event.")
    parent_event: str | None = Field(
        default=None, description="Parent event id, when the event has one."
    )
    event_kind: str = Field(description="Registry kind of the event.")
    primitive_type: str | None = Field(
        description="L4 primitive the event carries; null for an "
        "out-of-band partial event row."
    )
    status: str = Field(
        description="Progress projection status of the event: the current "
        "event is active, earlier ones completed."
    )
    summary: str | None = Field(
        default=None, description="Optional one-line summary of the event."
    )
    content: str | None = Field(
        default=None, description="Optional content payload of the event."
    )


class CurrentEventDto(TaskProgressEventDto):
    """§6.4 current_event: a progress entry plus occurred_at.

    The occurred_at field is the 8th field the service projects on the
    current event and must not be dropped at the DTO layer.
    """

    occurred_at: datetime | None = Field(
        default=None,
        description="Wall-clock time the current event was accepted, when recorded.",
    )


class TaskProgress(BaseModel):
    """Task progress block (spec §6.5 schema name, spec §6.4 response)."""

    total_events: int = Field(
        description="Total events in the projection, before any visibility "
        "filtering applied by the read."
    )
    events: list[TaskProgressEventDto] = Field(
        description="Event progress entries, oldest first; only the trunk "
        "chain when trunk_events filtering is requested."
    )


class ContextCollabStatusResponse(BaseModel):
    """Status and task-progress response (spec §6.5 schema name, §6.4)."""

    collab_context_id: str = Field(description="Id of the collaboration context.")
    context_version: int = Field(description="Current context version.")
    current_snapshot: CurrentSnapshotDto = Field(
        description="Snapshot anchored on the current event."
    )
    current_event: CurrentEventDto | None = Field(
        description="The event the snapshot is anchored on, when one exists."
    )
    task_progress: TaskProgress = Field(
        description="Event progress projection of the context."
    )


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
        executor_refs=(
            [_principal_id(ref) for ref in snapshot["executor_refs"]]
            if "executor_refs" in snapshot
            else None
        ),
        method_kind=snapshot["method_kind"],
        progress=ProgressDto(**progress),
    )


def created_collet_to_dto(
    collet: Collet, command: CollaborationCreateCommand, events: list[EventRecord]
) -> ContextCollabCreatedResultDto:
    """Preserve the creation wire receipt without adding read views to Collet.

    The successful create contract guarantees a root Task in ``created`` and
    its initial ``task.created`` event. Echo the accepted creation facts, using
    the domain parser for the canonical Method spelling. Select immutable creation
    evidence at the returned revision, not the latest status; concurrent appends
    must not turn this receipt into a later-state view. Collet has no event cursor.
    """
    initial_event = next(
        (
            event
            for event in events
            if event.collab_context_id == collet.collet_id
            and event.context_version_after == collet.version
            and event.event_kind == "task.created"
            and event.primitive_type == "Task"
            and event.parent_event is None
        ),
        None,
    )
    if initial_event is None:
        raise ValueError("created Collet must have initial event evidence")
    return ContextCollabCreatedResultDto(
        collab_context_id=collet.collet_id,
        context_version=collet.version,
        current_snapshot=CurrentSnapshotDto(
            objective=command.objective,
            status="created",
            principal_ref=command.principal_ref.principal_id,
            executor_ref=(
                command.executor_ref.agent_id if command.executor_ref else None
            ),
            method_kind=MethodKind.from_wire(command.method_kind).value,
            progress=ProgressDto(percent=0, current_event_id=initial_event.event_id),
        ),
        current_event=ContextEventBriefDto(
            event_id=initial_event.event_id,
            parent_event=None,
            event_kind="task.created",
            primitive_type="Task",
        ),
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
        current_snapshot=(
            _snapshot_to_dto(result.current_snapshot)
            if result.current_snapshot is not None
            else None
        ),
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
