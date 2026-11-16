"""Context collab 服务用例编排(G1 校验 + G4 状态机 + G3 投影 + 仓储)。

本文件实现 ``create_collet``(spec §6.2)、显式 L4 Work ``start``、
``append_event``(spec §6.3,接入 G4 状态机与守卫)与 ``get_status``(spec §6.4,
纯读路径:visibility 过滤 → G3 投影 → 进度统计,不写任何行)。
``get_events`` 返回 G1 可见的 L4 事件事实,供库调用方读取成果引用等 payload。
服务层不感知 HTTP/Pydantic,仓储与授权都以协议注入。
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
    StructuralContextCollabAuthorizer,
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
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.product_contract import (
    validate_product_event,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.schema import (
    validate_event_body,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import Collet
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.work import (
    prepare_event_append,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
    parse_method_kinds,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    METHOD_PROFILE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.initialization import (
    ResolvedMethodSpec,
    initialize_top_level_binding,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _now,
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    product_definition,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_method_kind,
    creation_profile_ref,
    creation_product_kind,
    creation_payload,
    creation_workflow_kind,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    resolve_event_contract,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    AppendEventRecordRequest,
    ContextCollabRepositoryProtocol,
    CreateColletRecordRequest,
    InitialEventDraft,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    ContextCollabServiceProtocol,
    ContextCollabStatusProjection,
    ColletCreateCommand,
    EventAcceptedResult,
    EventAppendCommand,
    TaskProgressProjectionDTO,
    WorkStartCommand,
)
from injector import inject

CREATION_EVENT_KIND = "task.created"
CREATION_PRIMITIVE_TYPE = "Task"


def _required_str(value: object, field: str) -> str:
    """必填字符串校验;空串/非字符串抛 ``ContextCollabValidationError``。"""
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{field} must be a non-empty string, got: {value!r}"
        )
    return value


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


class ContextCollabService(ContextCollabServiceProtocol):
    """L4 北向创建、Work.start、追加与只读查询;复用 G1/G4 与原子写入。"""

    @inject
    def __init__(
        self,
        repository: ContextCollabRepositoryProtocol,
        method_store: MethodStoreProtocol,
        authorizer: ContextCollabAuthorizerProtocol = StructuralContextCollabAuthorizer(),
        *,
        product_catalog: ProductCatalog,
    ) -> None:
        self._product_catalog = product_catalog
        self._repository = repository
        self._method_store = method_store
        self._authorizer = authorizer

    def create_collet(
        self, command: ColletCreateCommand, context: ActorContext
    ) -> Collet:
        """创建用例(spec §6.2):校验 → G1 check → 仓储创建 → L5 顶层投影
        → 返回持久化 Collet。

        method/wire 与 objective 校验后走 G1 结构授权;产品选择元数据原样
        透传,本用例不解析/校验 L6 Profile,也不自动选择版本或派生角色引用。
        Single executor 与顶层 method 白名单由 G1 校验。创建不设幂等键
        (spec 作者裁决):重复调用即重复创建。
        """
        ## TODO: remove profile id ref
        objective = _required_str(command.objective, "objective")
        method_kind = MethodKind.from_wire(command.method_kind).value
        self._authorizer.check_create(
            method_kind=method_kind,
            executor_ref=command.executor_ref,
            actor=context,
        )
        facts = creation_payload(
            method_kind=method_kind,
            executor_ref=command.executor_ref,
            objective=objective,
            resource_refs=tuple(command.resource_refs),
            product_kind=command.product_kind,
            workflow_kind=command.workflow_kind,
            product_profile_ref=command.product_profile_ref,
        )
        record = self._repository.create_collet(
            CreateColletRecordRequest(
                space=command.to_space,
                principal_ref=command.principal_ref,
                method_kind=method_kind,
                executor_ref=command.executor_ref,
                objective=objective,
                resource_refs=tuple(command.resource_refs),
                product_kind=command.product_kind,
                workflow_kind=command.workflow_kind,
                product_profile_ref=command.product_profile_ref,
                creation_facts=facts,
                initial_event=InitialEventDraft(
                    event_kind=CREATION_EVENT_KIND,
                    primitive_type=CREATION_PRIMITIVE_TYPE,
                    actor_ref=context.actor,
                    payload=facts,
                ),
            )
        )

        # 仅按显式 Method/身份事实初始化 L5 绑定,不读取 L6 Profile。
        # store 写失败原样上抛;同上下文重复投影幂等。
        # TODO
        initialize_top_level_binding(
            self._method_store,
            current_axes(),
            record.collet.collet_id,
            ResolvedMethodSpec(
                method_kind=method_kind,
                profile_versions={
                    kind.value: METHOD_PROFILE_REGISTRY[kind.value].profile_version
                    for kind in parse_method_kinds(method_kind)
                },
                role_binding_refs=(),
                principal_id=command.principal_ref.principal_id,
                executor_id=command.executor_ref.agent_id if command.executor_ref else None,
            ),
            context,
            now=_now(),
        )

        return record.collet

    def start(
        self, collab_context_id: str, command: WorkStartCommand, context: ActorContext
    ) -> EventAcceptedResult:
        """L4 Work.start, not a product action or an arbitrary event append.

        The root Task and pinned lifecycle are resolved by the shared northbound
        pipeline. G1 (including admission), product contracts, G4 guards and both
        Task/Collet revision fences remain authoritative. No delivery side effect.
        """
        # TODO: invoke l4 task start operate
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
    ) -> EventAcceptedResult:
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
            collet, status.task, status.creation_facts, status.product_instance, command, context
        )

        # Fast stale-request rejection precedes sequence checks; repository CAS
        # remains authoritative for a writer racing after this read.
        ensure_expected_version(command.expected_version, collet.version)
        validate_product_event(
            catalog=self._product_catalog,
            creation_facts=status.creation_facts,
            product_instance=status.product_instance,
            task=status.task,
            event_kind=command.event_kind,
            primitive_type=command.primitive_type,
            visibility=command.visibility,
            payload=command.payload,
            actor=context.actor,
            parent_event=command.parent_event,
            events=status.events,
        )
        profile = product_definition(
            product_kind=creation_product_kind(status.creation_facts),
            workflow_kind=creation_workflow_kind(status.creation_facts),
            method_kind=creation_method_kind(status.creation_facts),
            profile_ref=creation_profile_ref(status.creation_facts),
            catalog=self._product_catalog,
        )
        machine_ref = (
            resolve_event_contract(profile).state_machine_ref if profile is not None else None
        )
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
            workflow_kind=machine_ref.profile_id
            if machine_ref
            else creation_workflow_kind(status.creation_facts),
            workflow_version=machine_ref.version if machine_ref else None,
            require_transition=require_transition,
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
        不读取持久化视图;``current_event`` 按 collet 行指针取投影
        条目,指针事件不可见时为 ``None``。
        """
        status = self._repository.get_current_status(collab_context_id)
        if status is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        collet = status.collet
        visible_events = self._authorizer.filter_visible_events(
            collet, status.task, status.creation_facts, status.product_instance, status.events, context
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
        visible_ids = frozenset(event.event_id for event in visible_events)
        tasks = status.tasks or ((status.task,) if status.task is not None else ())
        visible_tasks = tuple(
            task
            for task in tasks
            if task.last_event_ref is None or task.last_event_ref in visible_ids
        )
        progress = build_progress(
            visible_tasks,
            current_event_id=collet.current_event_id,
            visible_event_ids=visible_ids,
        )
        snapshot = None
        if status.task is not None and status.task in visible_tasks:
            # Compatibility response only; authoritative Work state is status.task.
            view = status.current_snapshot
            snapshot = view.to_dict()
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
        return list(
            self._authorizer.filter_visible_events(
                status.collet,
                status.task,
                status.creation_facts,
                status.product_instance,
                status.events,
                context,
            )
        )
