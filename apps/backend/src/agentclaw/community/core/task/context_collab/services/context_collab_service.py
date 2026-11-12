"""Context collab 服务用例编排(G1 校验 + G4 状态机 + G3 投影 + 仓储)。

本文件实现 ``create_context``(spec §6.2)、``append_event``
(spec §6.3,接入 G4 状态机与守卫)与 ``get_status``(spec §6.4,
纯读路径:visibility 过滤 → G3 投影 → 进度统计,不写任何行)。
服务层不感知 HTTP/Pydantic,仓储与授权都以协议注入。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
    StructuralContextCollabAuthorizer,
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
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    GUARD_REGISTRY,
    GuardEvaluationInput,
    MACHINE_REGISTRY,
    resolve_transition,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ContextSpec,
    CurrentSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
)
from agentclaw.community.core.task.context_collab.l6_products.profiles import (
    ProductProfile,
    WorkflowKind,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    AppendEventRecordRequest,
    ContextCollabRepositoryProtocol,
    ContextStatusRecord,
    CreateContextRecordRequest,
    InitialEventDraft,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    ContextCollabCreatedResult,
    ContextCollabRunResultDTO,
    ContextCollabServiceProtocol,
    ContextCollabStatusProjection,
    ContextCreateCommand,
    EventAcceptedResult,
    EventAppendCommand,
    TaskProgressProjectionDTO,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.context_collab_runtime import (
    ContextCollabRunRequest,
    ContextCollabRuntimePlugin,
)

logger = get_logger(__name__)

CREATION_EVENT_KIND = "task.created"
CREATION_PRIMITIVE_TYPE = "Task"


def _required_str(value: object, field: str) -> str:
    """必填字符串校验;空串/非字符串抛 ``ContextCollabValidationError``。"""
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{field} must be a non-empty string, got: {value!r}"
        )
    return value


def _optional_str(value: object, field: str) -> str | None:
    """Validate an optional identifier without inventing a fallback value."""
    if value is None:
        return None
    return _required_str(value, field)


def _optional_enum_value(value: object, allowed: set[str], field: str) -> str | None:
    """可空枚举校验;非 None 时必须命中已注册 wire 值。"""
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise ContextCollabValidationError(
            f"unknown {field} value: {value!r}, allowed: {sorted(allowed)}"
        )
    return value


def _created_snapshot_projection(snapshot: CurrentSnapshot) -> dict:
    """把 ``CurrentSnapshot`` 投影为 spec §6.2 响应形态。

    §6.2 响应中 ``principal_ref`` / ``executor_ref`` 是裸 ID 字符串
    (非引用对象);返回新 dict(嵌套 progress 也是新构造),可安全改写。
    """
    data = snapshot.to_dict()
    data["principal_ref"] = snapshot.principal_ref.principal_id
    data["executor_ref"] = (
        snapshot.executor_ref.agent_id if snapshot.executor_ref else None
    )
    return data


def _created_event_projection(event: EventRecord) -> dict:
    """初始事件在创建响应里的基础字段投影(spec §6.2 ``current_event``)。"""
    return {
        "event_id": event.event_id,
        "parent_event": event.parent_event,
        "event_kind": event.event_kind,
        "primitive_type": event.primitive_type,
    }


_EVENT_PROJECTION_FIELDS = (
    "event_id",
    "event_seq",
    "parent_event",
    "event_kind",
    "primitive_type",
    "actor_id",
    "actor_type",
    "execution_id",
    "context_version",
    "status",
    "summary",
    "content",
    "artifacts",
    "created_at",
)


def _event_projection_dict(projection: EventProjection) -> dict:
    """投影事件 → ``task_progress.events[]`` 条目(spec §6.4)。

    字段集合不含 ``steps``(§12.2 #5);值全部取自 G3 投影,不回读仓储。
    """
    return {field: getattr(projection, field) for field in _EVENT_PROJECTION_FIELDS}


def _last_payload_percent(events: Sequence[EventRecord]) -> int | None:
    """按事件顺序取最后一个 payload 携带整数 ``percent`` 的值。

    输入是已经过 visibility 过滤、未做 trunk 过滤的完整可见事件序列
    (get_status 从全量事件推导,§12.2 #4 / R6),保证 ``trunk_events``
    开关不影响 percent,也不向非参与方泄露 private 分支的执行进度。
    ``bool`` 不是合法 percent,跳过。
    """
    last_percent = None
    for record in events:
        if record.payload is None:
            continue  # NULL payload 的部分行不携带 percent
        percent = record.payload.get("percent")
        if isinstance(percent, bool) or not isinstance(percent, int):
            continue
        last_percent = percent
    return last_percent


def _resolve_event_status(
    collet: Collet, command: EventAppendCommand, context: ActorContext
) -> "str | None":
    """G4 状态机裁决(spec §4):返回事件接纳后的新 status。

    collet 的 ``workflow_kind`` 未命中 ``MACHINE_REGISTRY``,或当前状态
    对该事件无规则命中 → status 不变(事件仍接纳)。守卫输入按
    Controller Ruling R4 用值语义构造:actor 取已认证调用者,responsible
    取 collet 快照 principal,executor 取创建参数 executor。guard
    FAIL / BLOCKED 由 ``resolve_transition`` 抛 ``ContextCollabValidationError``
    自然向外传。
    """
    if collet.current_snapshot is None:
        # 可空快照(外部部分行):无状态可裁决,事件照常接纳。
        return None
    current_status = collet.current_snapshot.status
    machine = MACHINE_REGISTRY.get(collet.context_spec.workflow_kind)
    if machine is None:
        return current_status
    matched_rules = [
        rule
        for rule in machine.transitions
        if rule.event_name == command.event_kind and current_status in rule.from_states
    ]
    if not matched_rules:
        return current_status
    guard_input = GuardEvaluationInput(
        actor=context.actor,
        responsible=collet.current_snapshot.principal_ref,
        executor=collet.context_spec.executor_ref,
        payload=command.payload,
    )
    guard_results = {
        guard_ref: GUARD_REGISTRY[guard_ref](guard_input)
        for rule in matched_rules
        for guard_ref in rule.guard_refs
    }
    new_status = resolve_transition(
        machine, current_status, command.event_kind, guard_results
    )
    return current_status if new_status is None else new_status


class ContextCollabService(ContextCollabServiceProtocol):
    """三个北向用例的服务实现;P1 默认注入结构授权器。"""

    @inject
    def __init__(
        self,
        repository: ContextCollabRepositoryProtocol,
        runtime: ContextCollabRuntimePlugin,
        authorizer: ContextCollabAuthorizerProtocol = StructuralContextCollabAuthorizer(),
    ) -> None:
        self._repository = repository
        self._runtime = runtime
        self._authorizer = authorizer

    def create_context(
        self, command: ContextCreateCommand, context: ActorContext
    ) -> ContextCollabCreatedResult:
        """创建用例(spec §6.2):校验 → G1 check → 仓储创建 → 组装结果。

        枚举校验(method_kind / product_kind / workflow_kind)与必填字符串
        校验(objective)在本层完成;Single 必须有 executor 由结构授权器
        完成。创建不设幂等键(spec 作者裁决):重复调用即重复创建。
        """
        objective = _required_str(command.objective, "objective")
        spec = ContextSpec(
            space=command.space,
            principal_ref=command.principal_ref,
            method_kind=MethodKind.from_wire(command.method_kind).value,
            executor_ref=command.executor_ref,
            objective=objective,
            resource_refs=tuple(command.resource_refs),
            product_kind=_optional_enum_value(
                command.product_kind,
                {profile.value for profile in ProductProfile},
                "product_kind",
            ),
            workflow_kind=_optional_enum_value(
                command.workflow_kind,
                {kind.value for kind in WorkflowKind},
                "workflow_kind",
            ),
            executor_owner_id=_optional_str(
                command.executor_owner_id,
                "executor_owner_id",
            ),
        )
        self._authorizer.check_create(spec, context)

        record = self._repository.create_context(
            CreateContextRecordRequest(
                context_spec=spec,
                initial_event=InitialEventDraft(
                    event_kind=CREATION_EVENT_KIND,
                    primitive_type=CREATION_PRIMITIVE_TYPE,
                    actor_ref=context.actor,
                    payload={"objective": objective},
                ),
            )
        )

        # 用初始事件的 event_id 回填结果快照的进度引用(仓储初始快照
        # progress.current_event_id 为 None,权威事件指针在 Event record)。
        snapshot = _created_snapshot_projection(record.collet.current_snapshot)
        snapshot["progress"]["current_event_id"] = record.initial_event.event_id
        return ContextCollabCreatedResult(
            collab_context_id=record.collet.collab_context_id,
            context_version=record.collet.context_version,
            current_snapshot=snapshot,
            current_event=_created_event_projection(record.initial_event),
        )

    def append_event(
        self, collab_context_id: str, command: EventAppendCommand, context: ActorContext
    ) -> EventAcceptedResult:
        """追加事件用例(spec §6.3):加载 → G4 校验 → G1 check →
        状态机裁决 → 仓储追加 → 组装结果。

        涉及聚合状态的并发语义由仓储把守:``parent_event`` 存在性与
        ``expected_version`` 乐观并发都在仓储持锁事务内裁决,相关错误
        (VersionConflict / ParentEventError 等)原样上抛,本层不预查、
        不吞。追加不设幂等键(spec 作者裁决):重复提交即重复事件,
        防重由调用方保证。``snapshot_updater`` 是只替换 status 的纯函数,
        由仓储在其行锁内求值,避免陈旧写。
        """
        status = self._repository.get_current_status(collab_context_id)
        if status is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        collet = status.collet
        validate_event_body(
            event_kind=command.event_kind,
            primitive_type=command.primitive_type,
            visibility=command.visibility,
            payload=command.payload,
        )
        self._authorizer.check_append(collet, command, context)

        new_status = _resolve_event_status(collet, command, context)

        def _update_status(
            snapshot: CurrentSnapshot,
        ) -> CurrentSnapshot:
            if new_status is None:
                return snapshot
            return replace(snapshot, status=new_status)

        record = self._repository.append_event(
            collab_context_id,
            AppendEventRecordRequest(
                expected_version=command.expected_version,
                event_kind=command.event_kind,
                primitive_type=command.primitive_type,
                actor_ref=command.actor_ref,
                parent_event=command.parent_event,
                visibility=command.visibility,
                payload=command.payload,
                snapshot_updater=_update_status,
            ),
        )
        return EventAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            event_id=record.event.event_id,
            parent_event=record.event.parent_event,
            event_kind=record.event.event_kind,
            primitive_type=record.event.primitive_type,
            context_version=record.collet.context_version,
            current_status=(
                record.collet.current_snapshot.status
                if record.collet.current_snapshot is not None
                else None
            ),
        )

    async def run_context(
        self,
        collab_context_id: str,
        context: ActorContext,
    ) -> ContextCollabRunResultDTO:
        """Start the bound executor, then append the authoritative start event.

        Runtime invocation and event persistence intentionally remain two
        independent operations in this phase. A concurrent append after the
        pre-read is rejected by the repository's expected-version check rather
        than overwriting the newer Context state.
        """
        status = self._repository.get_current_status(collab_context_id)
        if status is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        collet = status.collet
        snapshot = collet.current_snapshot
        if snapshot is None:
            raise ContextCollabValidationError(
                "collaboration context cannot run without a current snapshot"
            )
        if snapshot.status != "created":
            raise ContextCollabValidationError(
                "collaboration context can run only from status 'created', "
                f"got {snapshot.status!r}"
            )
        executor = collet.context_spec.executor_ref
        if executor is None:
            raise ContextCollabValidationError(
                "collaboration context cannot run without an executor_ref"
            )
        todo_refs = [
            ref
            for ref in collet.context_spec.resource_refs
            if ref.resource_type == "todo"
        ]
        if len(todo_refs) != 1:
            raise ContextCollabValidationError(
                "collaboration context run requires exactly one todo resource_ref"
            )

        runtime_result = await self._runtime.run(
            ContextCollabRunRequest(
                collab_context_id=collab_context_id,
                executor_id=executor.agent_id,
                principal_id=collet.context_spec.principal_ref.principal_id,
                objective=collet.context_spec.objective,
                todo_id=todo_refs[0].resource_id,
                executor_owner_id=collet.context_spec.executor_owner_id,
            )
        )
        payload = {"run_id": runtime_result.run_id}
        if runtime_result.session_id is not None:
            payload["session_id"] = runtime_result.session_id
        executor_actor = PrincipalRef("bot", executor.agent_id)
        accepted = self.append_event(
            collab_context_id,
            EventAppendCommand(
                expected_version=collet.context_version,
                event_kind="task.started",
                primitive_type="Task",
                actor_ref=executor_actor,
                parent_event=collet.current_event_id,
                visibility="workspace",
                payload=payload,
            ),
            ActorContext(actor=executor_actor, source=context.source),
        )
        if accepted.current_status is None:
            raise ContextCollabValidationError(
                "collaboration context run did not produce a current status"
            )
        return ContextCollabRunResultDTO(
            collab_context_id=collab_context_id,
            run_id=runtime_result.run_id,
            session_id=runtime_result.session_id,
            context_version=accepted.context_version,
            current_status=accepted.current_status,
        )

    async def refresh_run(
        self,
        collab_context_id: str,
        context: ActorContext,
    ) -> ContextCollabStatusProjection:
        """Project the executor's real run state into the Context lifecycle.

        Runtime reads are side-effect free. Only a confirmed ``COMPLETED`` run
        appends ``task.completed``; non-terminal and failed terminal states leave
        the Context ``in_progress`` for later inspection or recovery.
        """
        status = self._repository.get_current_status(collab_context_id)
        if status is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        collet = status.collet
        snapshot = collet.current_snapshot
        if snapshot is None:
            raise ContextCollabValidationError(
                "collaboration context cannot refresh without a current snapshot"
            )
        if snapshot.status == "done":
            return self._project_status_record(
                status, trunk_events=False, context=context
            )
        if snapshot.status != "in_progress":
            raise ContextCollabValidationError(
                "collaboration context can refresh only from status "
                f"'in_progress', got {snapshot.status!r}"
            )

        started_event = next(
            (
                event
                for event in reversed(status.events)
                if event.event_kind == "task.started"
            ),
            None,
        )
        payload = started_event.payload if started_event is not None else None
        run_id = payload.get("run_id") if isinstance(payload, dict) else None
        if not isinstance(run_id, str) or not run_id.strip():
            raise ContextCollabValidationError(
                "in_progress collaboration context requires a task.started event "
                "with a non-empty run_id"
            )

        runtime_status = await self._runtime.get_run(run_id)
        if runtime_status.status in {"PENDING", "RUNNING"}:
            return self._project_status_record(
                status, trunk_events=False, context=context
            )
        if runtime_status.status != "COMPLETED":
            logger.warning(
                "Context executor run did not complete successfully: "
                "collab_context_id=%s run_id=%s status=%s error=%r",
                collab_context_id,
                run_id,
                runtime_status.status,
                runtime_status.error,
            )
            return self._project_status_record(
                status, trunk_events=False, context=context
            )

        executor = collet.context_spec.executor_ref
        if executor is None:
            raise ContextCollabValidationError(
                "collaboration context cannot complete without an executor_ref"
            )
        completion_payload: dict[str, object] = {"run_id": run_id}
        if runtime_status.result is not None:
            completion_payload["result"] = runtime_status.result
        if runtime_status.session_id is not None:
            completion_payload["session_id"] = runtime_status.session_id
        if runtime_status.completed_at is not None:
            completion_payload["completed_at"] = runtime_status.completed_at
        executor_actor = PrincipalRef("bot", executor.agent_id)
        try:
            self.append_event(
                collab_context_id,
                EventAppendCommand(
                    expected_version=collet.context_version,
                    event_kind="task.completed",
                    primitive_type="Task",
                    actor_ref=executor_actor,
                    parent_event=collet.current_event_id,
                    visibility="workspace",
                    payload=completion_payload,
                ),
                ActorContext(actor=executor_actor, source=context.source),
            )
        except ContextCollabVersionConflict:
            concurrent = self._repository.get_current_status(collab_context_id)
            if (
                concurrent is not None
                and concurrent.collet.current_snapshot is not None
                and concurrent.collet.current_snapshot.status == "done"
            ):
                return self._project_status_record(
                    concurrent, trunk_events=False, context=context
                )
            raise

        completed = self._repository.get_current_status(collab_context_id)
        if completed is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        return self._project_status_record(
            completed, trunk_events=False, context=context
        )

    def get_status(
        self, collab_context_id: str, *, trunk_events: bool, context: ActorContext
    ) -> ContextCollabStatusProjection:
        """查询用例(spec §6.4):纯读路径,不写任何行、不推进版本。"""
        status = self._repository.get_current_status(collab_context_id)
        if status is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        return self._project_status_record(
            status, trunk_events=trunk_events, context=context
        )

    def _project_status_record(
        self,
        status: ContextStatusRecord,
        *,
        trunk_events: bool,
        context: ActorContext,
    ) -> ContextCollabStatusProjection:
        """Build the read projection from one already-loaded aggregate record."""
        collet = status.collet
        visible_events = self._authorizer.filter_visible_events(
            collet, status.events, context
        )
        # 投影始终基于全量可见事件,不因 trunk 开关退化;
        # trunk 过滤 = 当前活动链(active_chain_ids)在投影条目上的子集。
        projection = project_events(
            visible_events,
            current_event_id=collet.current_event_id,
        )
        trunk_ids = None
        if trunk_events:
            trunk_ids = active_chain_ids(visible_events, collet.current_event_id)
        listed_projection = (
            projection
            if trunk_ids is None
            else [entry for entry in projection if entry.event_id in trunk_ids]
        )
        progress = build_progress(
            projection,
            current_event_id=collet.current_event_id,
            last_percent=_last_payload_percent(visible_events),
        )
        # 可空快照(外部部分行)返回 null;事件链仍照常投影与统计。
        # to_dict 返回全新嵌套 dict,替换 progress 不会写回 collet 行。
        snapshot = (
            None
            if collet.current_snapshot is None
            else collet.current_snapshot.to_dict()
        )
        if snapshot is not None:
            snapshot["progress"] = progress.to_dict()
        current_event = None
        for entry in projection:
            if entry.event_id == collet.current_event_id:
                current_event = _event_projection_dict(entry)
                break
        return ContextCollabStatusProjection(
            collab_context_id=collet.collab_context_id,
            context_version=collet.context_version,
            current_snapshot=snapshot,
            current_event=current_event,
            task_progress=TaskProgressProjectionDTO(
                total_events=len(listed_projection),
                events=[_event_projection_dict(entry) for entry in listed_projection],
            ),
        )
