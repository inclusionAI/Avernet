"""L4 collaboration: create, Work, append and authorized read orchestration.

Policy adapters interpret optional upper-layer constraints; this service owns
schema validation, G1/G4 sequencing, Task resolution, CAS commits and G3 queries.
It never imports an application service, Profile, Binding, catalog or rich repo.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Generic, TypeVar

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.collet_creation import (
    ColletCreationAuthorizer,
    StructuralColletCreationAuthorizer,
)
from agentclaw.community.core.task.context_collab.g2_keep_reliable_live.reliability import (
    ensure_expected_version,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.observation import (
    EventProjection,
    active_chain_ids,
    build_progress,
    project_events,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.schema import (
    validate_event_body,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.legacy_collet import (
    LegacyCollet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextCollabStatusProjection,
    ContextServiceProtocol,
    EventAcceptedResult,
    TaskProgressProjectionDTO,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    AppendEventRecordRequest,
    ColletCreateCommand,
    EventAppendCommand,
    TaskCreateCommand,
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.start import (
    prepare_root_task,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.work import (
    prepare_event_append,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.service_ports import (
    CollaborationPolicy,
    CollaborationReadState,
    CollaborationStore,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task

StateT = TypeVar("StateT", bound=CollaborationReadState)


_EVENT_PROJECTION_FIELDS = (
    "event_id",
    "parent_event",
    "event_kind",
    "primitive_type",
    "status",
    "summary",
    "content",
)


def _event_projection_dict(projection: EventProjection) -> dict:
    """投影事件 → ``task_progress.events[]`` 条目(spec §6.4)。

    字段集合不含 ``steps``(§12.2 #5);值全部取自 G3 投影,不回读仓储。
    """
    return {field: getattr(projection, field) for field in _EVENT_PROJECTION_FIELDS}


class ContextService(ContextServiceProtocol, Generic[StateT]):
    """Concrete L4 service; injected ports carry storage and G1/G4 policies.

    StateT binds a reader and its policy adapter to the same read snapshot.
    L4 sees only CollaborationReadState; no extra metadata is interpreted here.
    """

    def __init__(
        self,
        repository: CollaborationStore[StateT],
        policy: CollaborationPolicy[StateT],
        authorizer: ColletCreationAuthorizer = StructuralColletCreationAuthorizer(),
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._authorizer = authorizer

    def create_collet(
        self, command: ColletCreateCommand, context: ActorContext
    ) -> LegacyCollet:
        self._authorizer.check_create_collet(command=command, actor=context)
        return self._repository.create_l4_collet(command, context.actor).collet

    def create_task(
        self, collab_context_id: str, command: TaskCreateCommand, context: ActorContext
    ) -> Task:
        self._authorizer.check_create_task(
            collet_id=collab_context_id, command=command, actor=context
        )
        return self._repository.create_l4_task(
            collab_context_id, command, context.actor
        ).task

    def start(
        self, collab_context_id: str, command: WorkStartCommand, context: ActorContext
    ) -> EventAcceptedResult:
        """L4 Work.start, not a product action or an arbitrary event append.

        When no root Task exists, construct a draft from the command's objective
        and actual L4 bindings. G1 (including admission), product contracts and G4
        validate before Task creation/start are committed together under Collet CAS.
        Existing Work keeps its identity and both revision fences. No delivery.
        """
        return self._append_event(
            collab_context_id,
            EventAppendCommand(
                expected_version=command.expected_version,
                event_kind="task.started",
                primitive_type="Task",
                actor_ref=context.actor,
                parent_event=None,
                visibility="public",
                payload={"note": command.note} if command.note else {},
            ),
            context,
            require_transition=True,
            start_command=command,
        )

    def append_event(
        self, collab_context_id: str, command: EventAppendCommand, context: ActorContext
    ) -> EventAcceptedResult:
        """追加事件用例(spec §6.3):加载 → G4 校验 → G1 check →
        状态机裁决 → 仓储追加 → 组装结果。

        涉及聚合状态的并发语义由仓储把守:``parent_event`` 存在性与
        ``expected_version`` 乐观并发都在仓储持锁事务内裁决,相关错误
        (VersionConflict / ParentEventError 等)原样上抛。本层先拒绝已陈旧版本、
        不吞。追加不设幂等键(spec 作者裁决):重复提交即重复事件,
        防重由调用方保证。G4 从 Task 求解状态,仓储以 TaskUpdate 同时
        围栏 Task/Collet 版本,拒绝读后并发变更造成的陈旧写。
        """
        return self._append_event(collab_context_id, command, context)

    def _append_event(
        self,
        collab_context_id: str,
        command: EventAppendCommand,
        context: ActorContext,
        *,
        require_transition: bool = False,
        start_command: WorkStartCommand | None = None,
    ) -> EventAcceptedResult:
        status = self._repository.get_current_status(collab_context_id)
        if status is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        task_creation = None
        if start_command is not None:
            if status.task is None:
                task_creation = prepare_root_task(status, start_command)
                # G1/G4 see the exact candidate Work and the unchanged LegacyCollet,
                # participation and method evidence. No write has happened yet.
                status = status.with_task(task_creation)
            elif (
                start_command.objective is not None
                and start_command.objective != status.task.objective
            ) or (
                start_command.completion_conditions
                and start_command.completion_conditions
                != status.task.completion_conditions
            ):
                raise ContextCollabValidationError(
                    "Work.start cannot redefine existing Task"
                )
        collet = status.collet
        validate_event_body(
            event_kind=command.event_kind,
            primitive_type=command.primitive_type,
            visibility=command.visibility,
            payload=command.payload,
        )
        self._policy.check_append(status, command, context)

        # Preserve early stale rejection before product-specific sequence checks.
        # SQL CAS remains authoritative against concurrent writes after this read.
        ensure_expected_version(command.expected_version, collet.version)
        lifecycle = self._policy.validate_append(status, command, context)
        prepared = prepare_event_append(
            collab_context_id,
            AppendEventRecordRequest(
                expected_version=command.expected_version,
                event_kind=command.event_kind,
                primitive_type=command.primitive_type,
                actor_ref=command.actor_ref,
                parent_event=command.parent_event,
                visibility=command.visibility,
                payload=command.payload,
            ),
            task=status.task,
            actor=context.actor,
            workflow_kind=lifecycle.workflow_kind,
            workflow_version=lifecycle.workflow_version,
            require_transition=require_transition,
        )
        if task_creation is not None:
            prepared = replace(
                prepared,
                request=replace(prepared.request, task_creation=task_creation),
            )
        record = prepared.commit(self._repository)
        return EventAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            event_id=record.event.event_id,
            parent_event=record.event.parent_event,
            event_kind=record.event.event_kind,
            primitive_type=record.event.primitive_type,
            context_version=record.collet.version,
            current_status=(record.task.status if record.task is not None else None),
        )

    def get_status(
        self, collab_context_id: str, *, trunk_events: bool, context: ActorContext
    ) -> ContextCollabStatusProjection:
        """查询用例(spec §6.4):纯读路径,不写任何行、不推进版本。

        Ruling R6/R9 / spec §12.2 #4:仓储全量返回事件(顺序即接纳
        顺序),Task 计数来自可见 Task 权威状态,已上报百分比要求可见事件证据;
        ``trunk_events=True`` 只过滤返回的 ``task_progress.events[]``
        条目 —— 主干链由 ``active_chain_ids``(current 沿 parent 回溯)
        推导,过滤不改 current_snapshot / current_event / 统计。
        visibility 过滤(G1)先于推导;最新状态证据不可见的 Task 不对外暴露。
        返回的 ``current_snapshot`` 从 Task 生成并填入 G3 实时进度,
        不读取持久化视图;``current_event`` 从完整事件流的最后一条接纳记录
        推导,该事件不可见时为 ``None``(不会回退到旧的可见事件)。
        """
        status = self._repository.get_current_status(collab_context_id)
        if status is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        collet = status.collet
        visible_events = self._policy.filter_visible_events(status, context)
        # Acceptance order is authoritative, including a latest event hidden by G1.
        current_event_id = status.events[-1].event_id if status.events else None
        # 投影始终基于全量可见事件,不因 trunk 开关退化;
        # trunk 过滤 = 当前活动链(active_chain_ids)在投影条目上的子集。
        projection = project_events(
            visible_events,
            current_event_id=current_event_id,
        )
        trunk_ids = None
        if trunk_events:
            trunk_ids = active_chain_ids(visible_events, current_event_id)
        listed_projection = (
            projection
            if trunk_ids is None
            else [entry for entry in projection if entry.event_id in trunk_ids]
        )
        visible_ids = frozenset(event.event_id for event in visible_events)
        tasks = status.tasks or ((status.task,) if status.task is not None else ())
        visible_tasks = tuple(
            task
            for task in tasks
            if task.last_event_ref is None or task.last_event_ref in visible_ids
        )
        progress = build_progress(
            visible_tasks,
            current_event_id=current_event_id,
            visible_event_ids=visible_ids,
        )
        snapshot = None
        if status.task is not None and status.task in visible_tasks:
            # Compatibility response only; authoritative Work state is status.task.
            view = status.current_snapshot
            if view is None:
                raise ContextCollabValidationError(
                    "Task response projection is missing"
                )
            snapshot = view.to_dict()
            snapshot["progress"] = progress.to_dict()
        current_event = None
        for entry in projection:
            if entry.event_id == current_event_id:
                current_event = {
                    **_event_projection_dict(entry),
                    "occurred_at": entry.occurred_at,
                }
                break
        return ContextCollabStatusProjection(
            collab_context_id=collet.collet_id,
            context_version=collet.version,
            current_snapshot=snapshot,
            current_event=current_event,
            task_progress=TaskProgressProjectionDTO(
                total_events=len(listed_projection),
                events=[_event_projection_dict(entry) for entry in listed_projection],
            ),
        )

    def get_events(
        self, collab_context_id: str, *, context: ActorContext
    ) -> list[EventRecord]:
        """只读查询可见事件及 payload;复用 G1,不绕过 private/workspace 边界。"""
        status = self._repository.get_current_status(collab_context_id)
        if status is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        return list(self._policy.filter_visible_events(status, context))
