"""L6 compatibility composition, adding legacy Method/product creation over L4.

The actual collaboration implementation and Service API live in L4. This class
only adds create_collaboration for existing HTTP/library callers; no Work or
query implementation lives here. New L4 consumers inject L4's own Protocol.
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service import (
    ContextService,
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
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_payload,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_policy import (
    ProductCollaborationPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.service_api import (
    CollaborationCreateCommand,
    ContextCollabServiceProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
    ContextStatusRecord,
    CreateColletRecordRequest,
    InitialEventDraft,
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


class ContextCollabService(
    ContextService[ContextStatusRecord], ContextCollabServiceProtocol
):
    """L6 combined creation compatibility over the canonical L4 service."""

    @inject
    def __init__(
        self,
        repository: ContextCollabRepositoryProtocol,
        method_store: MethodStoreProtocol,
        authorizer: ContextCollabAuthorizerProtocol = StructuralContextCollabAuthorizer(),
        *,
        product_catalog: ProductCatalog,
    ) -> None:
        super().__init__(
            repository,
            ProductCollaborationPolicy(product_catalog, authorizer),
            authorizer,
        )
        self._product_catalog = product_catalog
        self._repository = repository
        self._method_store = method_store
        self._authorizer = authorizer

    def create_collaboration(
        self, command: CollaborationCreateCommand, context: ActorContext
    ) -> Collet:
        """创建用例(spec §6.2):校验 → G1 check → 仓储创建 → L5 顶层投影
        → 返回持久化 Collet。

        method/wire 与 objective 校验后走 G1 结构授权;产品选择元数据原样
        透传,本用例不解析/校验 L6 Profile,也不自动选择版本或派生角色引用。
        Single executor 与顶层 method 白名单由 G1 校验。创建不设幂等键
        (spec 作者裁决):重复调用即重复创建。
        """
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
                executor_id=command.executor_ref.agent_id
                if command.executor_ref
                else None,
            ),
            context,
            now=_now(),
        )

        return record.collet
