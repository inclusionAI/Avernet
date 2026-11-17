"""Project explicit L6 Method relations into independent L5 binding/edge facts."""

from dataclasses import replace

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    MethodBinding,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionEdgeState,
    MethodCompositionEdge,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.initialization import (
    ResolvedMethodSpec,
    build_profile_binding,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
)
from agentclaw.community.core.task.context_collab.l6_products.topology import (
    product_method_binding_ref,
    product_relation_id,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
    StoreAxes,
)


def project_method_topology(
    store: MethodStoreProtocol,
    axes: StoreAxes,
    collet_id: str,
    profile: ProductProfileDefinition,
    spec: ResolvedMethodSpec,
    actor: ActorContext,
    *,
    now: str,
    healing: bool,
) -> MethodBinding:
    """Retry fills missing facts, preserving lifecycle/audit of existing facts.

    SQL and MethodStore are not one transaction. A partial write is an error,
    not successful instantiation; later recovery repairs the pinned topology.
    """
    bindings: dict[str, MethodBinding] = {}
    for method in profile.composition.required_methods:
        name, version = method.method_kind, method.profile_version
        binding_id = product_method_binding_ref(collet_id, profile, name)
        binding = store.get_binding(axes, binding_id, collet_id)
        if binding is None:
            binding = store.upsert_binding(
                axes,
                build_profile_binding(
                    collet_id,
                    binding_id,
                    replace(spec, method_kind=name, profile_versions={name: version}),
                    actor,
                    now=now,
                    bind_reason="create_collet_heal" if healing else "create_collet",
                    reason="product topology heal"
                    if healing
                    else "product topology projection",
                ),
            )
        if binding.method_names != (name,) or binding.profile_versions != {
            name: version
        }:
            raise ContextCollabValidationError(
                "binding does not match pinned Method Profile"
            )
        bindings[name] = binding
    for relation in profile.composition.relations:
        parent, child = (
            bindings[relation.parent_method],
            bindings[relation.child_method],
        )
        edge_id = product_relation_id(collet_id, relation)
        existing = store.get_composition_edge(axes, edge_id, collet_id)
        if existing is not None:
            if (
                existing.parent_binding != parent.method_binding_id
                or existing.child_binding != child.method_binding_id
                or existing.relation_kind != relation.relation
            ):
                raise ContextCollabValidationError(
                    "edge does not match pinned Method relation"
                )
            # DETACHED remains detached; recovery never replays a lifecycle action.
            continue
        if BindingState.TERMINATED in {parent.state, child.state}:
            continue
        store.insert_composition_edge(
            axes,
            MethodCompositionEdge(
                edge_id=edge_id,
                collab_context_id=collet_id,
                parent_binding=parent.method_binding_id,
                child_binding=child.method_binding_id,
                relation_kind=relation.relation,
                state=CompositionEdgeState.ACTIVE,
                transitioned_from="",
                transition_reason="product topology heal"
                if healing
                else "product topology projection",
                transitioned_by=actor.actor.principal_id,
                transitioned_at=now,
            ),
        )
    return bindings[profile.composition.required_methods[0].method_kind]
