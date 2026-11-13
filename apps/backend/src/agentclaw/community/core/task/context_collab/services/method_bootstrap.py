"""Application Method bootstrap/recovery orchestration.

Resolves L6 creation contracts into L5 facts. Layer-owned lifecycle operations
never import this module; the legacy constructor remains an application facade.
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    role_binding_ref,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodBinding,
    parse_method_kinds,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    METHOD_PROFILE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.initialization import (
    ResolvedMethodSpec,
    build_top_level_binding,
    find_top_level_binding,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.initialization import (
    initialize_top_level_binding as initialize_method,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    validate_product_spec,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.context_spec import (
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
    StoreAxes,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from injector import inject


def _resolve_method_spec(
    collet_id: str, spec: ContextSpec, catalog: ProductCatalog
) -> ResolvedMethodSpec:
    profile = validate_product_spec(spec, catalog)
    versions = (
        {item.method: item.version for item in profile.composition.required_methods}
        if profile is not None
        else {
            kind.value: METHOD_PROFILE_REGISTRY[kind.value].profile_version
            for kind in parse_method_kinds(spec.method_kind)
        }
    )
    return ResolvedMethodSpec(
        method_kind=spec.method_kind,
        profile_versions=versions,
        role_binding_refs=tuple(
            role_binding_ref(collet_id, role) for role in spec.role_bindings
        ),
        principal_id=spec.principal_ref.principal_id,
        executor_id=spec.executor_ref.agent_id if spec.executor_ref else None,
    )


def project_created_context(
    store: MethodStoreProtocol,
    axes: StoreAxes,
    collab_context_id: str,
    spec: ContextSpec,
    actor: ActorContext,
    *,
    now: str,
    catalog: ProductCatalog,
) -> MethodBinding:
    existing = find_top_level_binding(store, axes, collab_context_id)
    if existing is not None:
        return existing
    return initialize_method(
        store,
        axes,
        collab_context_id,
        _resolve_method_spec(collab_context_id, spec, catalog),
        actor,
        now=now,
    )


def ensure_top_level_binding(
    store: MethodStoreProtocol,
    axes: StoreAxes,
    collab_context_id: str,
    collab_repo: ContextCollabRepositoryProtocol,
    actor: ActorContext,
    *,
    now: str,
    catalog: ProductCatalog,
) -> MethodBinding:
    """读路径自愈缝(spec §6.4):顶层 binding 缺失时按需静默重建。

    P1 内存 store 重启归零而 Collet(DB 持久)仍在:本缝先查既有顶层
    binding,存在即返回权威对象;缺失则经 ``collab_repo`` 取 Collet 的
    ``context_spec`` 重建 ACTIVE 投影(``bind_reason="create_context_heal"``
    以在审计上区分自愈来源),collet 不存在抛 ``ContextCollabNotFoundError``。
    已声明弱化:已 terminate 的顶层 binding 在重启后与丢失不可区分,
    自愈会以 ACTIVE 复活;terminate 门槛(§3.2)仍能挡住对活 Collet 的
    终止,跨重启的"终止不可复活"由 P2 持久化修复。自愈只重建 binding,
    roster/成员行如实为空(§6.4)。
    """
    existing = find_top_level_binding(store, axes, collab_context_id)
    if existing is not None:
        return existing
    record = collab_repo.get_current_status(collab_context_id)
    if record is None:
        raise ContextCollabNotFoundError(
            f"collaboration context {collab_context_id!r} not found"
        )
    binding = build_top_level_binding(
        collab_context_id,
        _resolve_method_spec(collab_context_id, record.context_spec, catalog),
        actor,
        now=now,
        bind_reason="create_context_heal",
        reason="top-level projection heal (spec §6.4)",
    )
    return store.upsert_binding(axes, binding)


class ProductMethodBindingRecovery:
    """Application port adapter; preserves pinned versions and restart behavior."""

    @inject
    def __init__(
        self,
        store: MethodStoreProtocol,
        repository: ContextCollabRepositoryProtocol,
        catalog: ProductCatalog,
    ) -> None:
        self._store = store
        self._repository = repository
        self._catalog = catalog

    def recover(
        self, collab_context_id: str, actor: ActorContext, *, now: str
    ) -> MethodBinding:
        return ensure_top_level_binding(
            self._store,
            current_axes(),
            collab_context_id,
            self._repository,
            actor,
            now=now,
            catalog=self._catalog,
        )
