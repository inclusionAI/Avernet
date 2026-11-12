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
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    AppendEventRecordRequest,
    ContextCollabRepositoryProtocol,
    CreateContextRecordRequest,
    InitialEventDraft,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    ContextCollabCreatedResult,
    ContextCollabServiceProtocol,
    ContextCollabStatusProjection,
    ContextCreateCommand,
    EventAcceptedResult,
    EventAppendCommand,
    TaskProgressProjectionDTO,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    _now,
    current_axes,
    project_created_context,
)

CREATION_EVENT_KIND = "task.created"
CREATION_PRIMITIVE_TYPE = "Task"


def _required_str(value: object, field: str) -> str:
    """必填字符串校验;空串/非字符串抛 ``ContextCollabValidationError``。"""
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{field} must be a non-empty string, got: {value!r}"
        )
    return value


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
    collet: Collet,
    spec: ContextSpec,
    snapshot: CurrentSnapshot | None,
    command: EventAppendCommand,
    context: ActorContext,
) -> "str | None":
    """G4 状态机裁决(spec §4):返回事件接纳后的新 status。

    ``spec`` / ``snapshot`` 是仓储 record 的伴随投影字段(载体迁移自
    Collet 聚合根);``spec`` 的 ``workflow_kind`` 未命中
    ``MACHINE_REGISTRY``,或当前状态对该事件无规则命中 → status 不变
    (事件仍接纳)。守卫输入按 Controller Ruling R4 用值语义构造:
    actor 取已认证调用者,responsible 取快照 principal,executor 取
    创建参数 executor。guard FAIL / BLOCKED 由 ``resolve_transition``
    抛 ``ContextCollabValidationError`` 自然向外传。
    """
    if snapshot is None:
        # 可空快照(外部部分行):无状态可裁决,事件照常接纳。
        return None
    current_status = snapshot.status
    machine = MACHINE_REGISTRY.get(spec.workflow_kind)
    if machine is None:
        return current_status
    matched_rules = [
        rule
        for rule in machine.transitions
        if rule.event_name == command.event_kind
        and current_status in rule.from_states
    ]
    if not matched_rules:
        return current_status
    guard_input = GuardEvaluationInput(
        actor=context.actor,
        responsible=snapshot.principal_ref,
        executor=spec.executor_ref,
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
        method_store: MethodStoreProtocol,
        authorizer: ContextCollabAuthorizerProtocol = StructuralContextCollabAuthorizer(),
    ) -> None:
        self._repository = repository
        self._method_store = method_store
        self._authorizer = authorizer

    def create_context(
        self, command: ContextCreateCommand, context: ActorContext
    ) -> ContextCollabCreatedResult:
        """创建用例(spec §6.2):校验 → G1 check → 仓储创建 → L5 顶层投影
        → 组装结果。

        枚举校验(method_kind / product_kind / workflow_kind)与必填字符串
        校验(objective)在本层完成;Single 必须有 executor 与顶层 method
        白名单(Single / Open + Group)由结构授权器完成。创建不设幂等键
        (spec 作者裁决):重复调用即重复创建。
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
        )
        self._authorizer.check_create(spec, context)

        record = self._repository.create_context(
            CreateContextRecordRequest(
                context_spec=spec,
                initial_event=InitialEventDraft(
                    event_kind=CREATION_EVENT_KIND,
                    primitive_type=CREATION_PRIMITIVE_TYPE,
                    actor_ref=context.actor,
                    # 创建资源绑定事实随初始事件持久(事件承载,spec
                    # 2026-11-12-context-collab-l4-resource-binding);恒带
                    # key,空引用也写空列表——新行与存量行(payload 无 key)
                    # 判别有据。
                    payload={
                        "objective": objective,
                        "resource_refs": [
                            ref.to_dict() for ref in spec.resource_refs
                        ],
                    },
                ),
            )
        )

        # L5 顶层投影(2026-11-12 L5 method objects spec §3.2/§9.2):Collet
        # 落库成功后同步落顶层 ACTIVE MethodBinding——由上面的 ``spec`` 单向
        # 派生,不回写 ``context_spec``;store 写失败如实上抛,绝不让
        # "有 Collet 无 binding"的响应伪装成功。同上下文重复投影幂等。
        project_created_context(
            self._method_store,
            current_axes(),
            record.collet.collet_id,
            spec,
            context,
            now=_now(),
        )

        # 用初始事件的 event_id 回填结果快照的进度引用(仓储初始快照
        # progress.current_event_id 为 None,权威事件指针在 Event record)。
        snapshot = _created_snapshot_projection(record.current_snapshot)
        snapshot["progress"]["current_event_id"] = record.initial_event.event_id
        return ContextCollabCreatedResult(
            collab_context_id=record.collet.collet_id,
            context_version=record.collet.version,
            current_snapshot=snapshot,
            current_event=_created_event_projection(record.initial_event),
        )

    def append_event(
        self, collab_context_id: str, command: EventAppendCommand,
        context: ActorContext
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
        self._authorizer.check_append(
            collet, status.context_spec, command, context
        )

        new_status = _resolve_event_status(
            collet, status.context_spec, status.current_snapshot, command,
            context,
        )

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
            context_version=record.collet.version,
            current_status=(
                record.current_snapshot.status
                if record.current_snapshot is not None
                else None
            ),
        )

    def get_status(
        self, collab_context_id: str, *, trunk_events: bool,
        context: ActorContext
    ) -> ContextCollabStatusProjection:
        """查询用例(spec §6.4):纯读路径,不写任何行、不推进版本。

        Ruling R6/R9 / spec §12.2 #4:仓储全量返回事件(顺序即接纳
        顺序),percent / 统计 / current_event 一律从完整可见事件推导;
        ``trunk_events=True`` 只过滤返回的 ``task_progress.events[]``
        条目 —— 主干链由 ``active_chain_ids``(current 沿 parent 回溯)
        推导,过滤不改 current_snapshot / current_event / 统计。
        visibility 过滤(G1)先于推导,统计与列表都只反映 actor 可见的
        事件。返回的 ``current_snapshot`` 用 ``build_progress`` 的实时
        投影替换落库 progress;``current_event`` 按 collet 行指针取投影
        条目,指针事件不可见时为 ``None``。
        """
        status = self._repository.get_current_status(collab_context_id)
        if status is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        collet = status.collet
        visible_events = self._authorizer.filter_visible_events(
            collet, status.context_spec, status.events, context
        )
        # 投影始终基于全量可见事件,不因 trunk 开关退化;
        # trunk 过滤 = 当前活动链(active_chain_ids)在投影条目上的子集。
        projection = project_events(
            visible_events,
            current_event_id=collet.current_event_id,
        )
        trunk_ids = None
        if trunk_events:
            trunk_ids = active_chain_ids(
                visible_events, collet.current_event_id
            )
        listed_projection = (
            projection
            if trunk_ids is None
            else [
                entry for entry in projection
                if entry.event_id in trunk_ids
            ]
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
            if status.current_snapshot is None
            else status.current_snapshot.to_dict()
        )
        if snapshot is not None:
            snapshot["progress"] = progress.to_dict()
        current_event = None
        for entry in projection:
            if entry.event_id == collet.current_event_id:
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
                events=[
                    _event_projection_dict(entry) for entry in listed_projection
                ],
            ),
        )
