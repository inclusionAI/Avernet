"""Context collab 服务用例编排(G1 校验 + 仓储)。

本文件只实现 ``create_context``(spec §6.2);``append_event`` 与
``get_status`` 的用例在后续任务落地,先留桩保证协议完整。
服务层不感知 HTTP/Pydantic,仓储与授权都以协议注入。
"""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
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


class ContextCollabService(ContextCollabServiceProtocol):
    """三个北向用例的服务实现;P1 默认注入结构授权器。"""

    @inject
    def __init__(
        self,
        repository: ContextCollabRepositoryProtocol,
        authorizer: ContextCollabAuthorizerProtocol = StructuralContextCollabAuthorizer(),
    ) -> None:
        self._repository = repository
        self._authorizer = authorizer

    def create_context(
        self, command: ContextCreateCommand, context: ActorContext
    ) -> ContextCollabCreatedResult:
        """创建用例(spec §6.2):校验 → G1 check → 仓储创建 → 组装结果。

        枚举校验(method_kind / product_kind / workflow_kind)与必填字符串
        校验(request_id / objective)在本层完成;Single 必须有 executor 由
        结构授权器完成。仓储负责幂等:重复 ``create_request_id``
        返回原上下文(``replayed=True``)。
        """
        create_request_id = _required_str(command.request_id, "request_id")
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
                create_request_id=create_request_id,
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
            replayed=not record.created,
        )

    def append_event(
        self, collab_context_id: str, command: EventAppendCommand,
        context: ActorContext
    ) -> EventAcceptedResult:
        raise NotImplementedError("append_event use case lands in the next task")

    def get_status(
        self, collab_context_id: str, *, trunk_events: bool,
        context: ActorContext
    ) -> ContextCollabStatusProjection:
        raise NotImplementedError("get_status use case lands in the read-model task")
