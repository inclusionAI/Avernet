"""Adapt five-table business storage to the current product service seam.

Creation and reads are backed by `ac_context_collab_*`. Runtime state advances
remain refused explicitly until the five-table execution contract is adopted;
missing control facts are never fabricated for compatibility.
"""

from datetime import datetime, timezone

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    current_actor_context,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    MethodBinding,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionEdgeState,
    MethodCompositionEdge,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    RuntimeSnapshot,
    SessionRoute,
    StageRecord,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile import (
    ProductProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    compile_configuration,
    encode_composition,
)
from .product_relational_repository import SqlProductBusinessRepository
from ..product_profile_service.snapshot_codec import canonical


class ProductBusinessRepositoryAdapter:
    """Current service/repository adapter over the business five-table UoW."""

    def __init__(self, business: SqlProductBusinessRepository) -> None:
        self.business = business

    def create_profile(
        self, name, composition, stages, context, request_id
    ) -> ProductProfile:
        del request_id
        return self.business.create_profile(
            name, composition, stages, context
        ).definition

    def get_profile(self, profile_id: int) -> tuple[ProductProfile, PrincipalRef]:
        record = self.business.get_profile(profile_id)
        if not record.creator:
            raise Invalid("ProductProfile creator is missing")
        principal_type, principal_id = record.creator.split(":", 1)
        return record.definition, PrincipalRef(principal_type, principal_id)

    def create_collet(self, profile, content, context, request_id, *, multi_setup=None):
        del request_id, multi_setup
        history = self.business.create_collet(profile.id, content, context)
        return self._snapshot(profile, history)

    def get_collet(self, collet_id: int) -> RuntimeSnapshot:
        context = current_actor_context()
        history = self.business.get_collet(collet_id, context)
        profile = self.business.get_profile(history.collet.profile_id).definition
        return self._snapshot(profile, history)

    def locate(self, identity: int, kind: str) -> RuntimeSnapshot:
        context = current_actor_context()
        history = self.business.locate(identity, kind, context)
        profile = self.business.get_profile(history.collet.profile_id).definition
        return self._snapshot(profile, history)

    @staticmethod
    def _snapshot(profile: ProductProfile, history) -> RuntimeSnapshot:
        specs, policy = compile_configuration(profile.composition, profile.stages)
        now = datetime.now(timezone.utc).isoformat()
        encoded = encode_composition(profile.composition)
        bindings = tuple(
            MethodBinding(
                method_binding_id=f"product-v2:{history.collet.id}:{declaration.profile.method_kind}",
                collab_context_id=f"product-v2:{history.collet.id}",
                method_kind=declaration.profile.method_kind,
                method_names=(declaration.profile.method_kind,),
                profile_versions={
                    declaration.profile.method_kind: declaration.profile.profile_version
                },
                config={"policy": encoded["required_methods"][index]["policy"]},
                state=BindingState.ACTIVE,
                bind_reason="product profile instantiation",
                transitioned_from="BOUND",
                transition_reason="authorized product creation",
                transitioned_by=canonical(history.owner.to_dict()),
                transitioned_at=now,
            )
            for index, declaration in enumerate(profile.composition.required_methods)
        )
        by_kind = {
            binding.method_kind: binding.method_binding_id for binding in bindings
        }
        relations = tuple(
            MethodCompositionEdge(
                f"product-v2:{history.collet.id}:edge:{index}",
                f"product-v2:{history.collet.id}",
                by_kind[relation.source_method],
                by_kind[relation.target_method],
                relation.relation,
                CompositionEdgeState.ACTIVE,
                "",
                "profile composition",
                canonical(history.owner.to_dict()),
                now,
            )
            for index, relation in enumerate(profile.composition.relations)
        )
        parents = {
            str(rollout_id): predecessor
            for rollout_id, predecessor in history.predecessors.items()
        }
        records = [
            StageRecord(stage.stage_rollout_id)
            for rollout in history.rollouts
            for stage in rollout.stages
        ]
        return RuntimeSnapshot(
            history.collet,
            history.owner,
            1,
            history.current_rollout_id,
            list(history.rollouts),
            records,
            specs,
            bindings,
            relations,
            policy.execution_ref,
            policy.acceptance_ref,
            policy.overall_acceptance,
            stopped=False,
            parents=parents,
        )

    def replay(self, request_id, operation, payload, context):
        del request_id, operation, payload, context
        return None

    def save(self, snapshot, revision, **kwargs):
        del snapshot, revision, kwargs
        raise Invalid(
            "runtime state changes are not implemented for ac_context_collab business storage"
        )

    def late_delivery(self, collet_id, stage_id, attempt, route: SessionRoute):
        del collet_id, stage_id, attempt, route
        raise Invalid(
            "runtime delivery state is not implemented for ac_context_collab business storage"
        )

    def pending_intents(self, limit: int = 20):
        del limit
        raise Invalid(
            "runtime intents are not implemented for ac_context_collab business storage"
        )

    def claim_intent(self, identity: int) -> str:
        del identity
        raise Invalid(
            "runtime intents are not implemented for ac_context_collab business storage"
        )

    def finish_intent(self, identity: int, state: str, fence: str = ""):
        del identity, state, fence
        raise Invalid(
            "runtime intents are not implemented for ac_context_collab business storage"
        )
