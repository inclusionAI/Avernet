"""Application Method bootstrap/recovery orchestration.

This adapter translates L4 Collet/Task identities and the selected L6 profile
into L5 facts.  It never accepts or reconstructs a ContextSpec-like aggregate.
"""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
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
    initialize_top_level_binding as initialize_method,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    role_binding_refs,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_method_kind,
    creation_profile_ref,
    creation_product_kind,
    creation_workflow_kind,
    decode_creation_facts,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
    StoreAxes,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepositoryProtocol,
)


def _resolve_method_spec(
    collet_id: str,
    *,
    method_kind: str,
    principal_ref: PrincipalRef,
    executor_ref: AgentRef | None,
    profile: ProductProfileDefinition | None,
) -> ResolvedMethodSpec:
    versions = (
        {item.method: item.version for item in profile.composition.required_methods}
        if profile is not None
        else {
            kind.value: METHOD_PROFILE_REGISTRY[kind.value].profile_version
            for kind in parse_method_kinds(method_kind)
        }
    )
    return ResolvedMethodSpec(
        method_kind=method_kind,
        profile_versions=versions,
        role_binding_refs=role_binding_refs(collet_id, profile=profile)
        if profile is not None
        else (),
        principal_id=principal_ref.principal_id,
        executor_id=executor_ref.agent_id if executor_ref else None,
    )


def project_created_context(
    store: MethodStoreProtocol,
    axes: StoreAxes,
    collab_context_id: str,
    *,
    method_kind: str,
    principal_ref: PrincipalRef,
    executor_ref: AgentRef | None,
    profile: ProductProfileDefinition | None,
    context: ActorContext,
    now: str,
) -> MethodBinding:
    existing = find_top_level_binding(store, axes, collab_context_id)
    if existing is not None:
        return existing
    return initialize_method(
        store,
        axes,
        collab_context_id,
        _resolve_method_spec(
            collab_context_id,
            method_kind=method_kind,
            principal_ref=principal_ref,
            executor_ref=executor_ref,
            profile=profile,
        ),
        context,
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
    """Read-path recovery of the L5 top-level binding from persisted facts."""
    existing = find_top_level_binding(store, axes, collab_context_id)
    if existing is not None:
        return existing
    record = collab_repo.get_current_status(collab_context_id)
    if record is None:
        raise ContextCollabNotFoundError(
            f"collaboration context {collab_context_id!r} not found"
        )
    facts = decode_creation_facts(record.creation_facts)
    profile = None
    if creation_product_kind(facts) is not None:
        profile = catalog.get(
            creation_product_kind(facts),
            creation_profile_ref(facts).version
            if creation_profile_ref(facts) is not None
            else 1,
        )
    return store.upsert_binding(
        axes,
        build_top_level_binding(
            collab_context_id,
            _resolve_method_spec(
                collab_context_id,
                method_kind=creation_method_kind(facts),
                principal_ref=record.collet.initiator_ref,
                executor_ref=(record.task.executor_ref if record.task is not None else None),
                profile=profile,
            ),
            actor,
            now=now,
            bind_reason="create_collet_heal",
            reason="top-level projection heal",
        ),
    )


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

    def recover(self, collab_context_id: str, actor: ActorContext, *, now: str) -> MethodBinding:
        return ensure_top_level_binding(
            self._store,
            current_axes(),
            collab_context_id,
            self._repository,
            actor,
            now=now,
            catalog=self._catalog,
        )
