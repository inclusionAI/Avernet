"""Joint composition/YAML validation and strict immutable configuration codec."""

from ..profile_record import ProductProfileRecord

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    MethodProfile,
    get_profile,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmStagesPolicy,
    SwarmWorkflowPolicy,
    OpenMethodPolicy,
    SwarmMethodPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionRelation,
    validate_composition,
    detect_cycle,
)
from agentclaw.community.core.task.context_collab.l5_methods.value_codec import (
    encode,
    decode,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodCompositionTemplate,
    MethodTemplate,
    MethodRelationTemplate,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.stages_yaml import (
    compile_stages,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile import (
    ProductProfile,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    shape,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow import (
    WORKFLOW_TYPES,
)
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    OpenAdmissionPolicy,
    BotSelfApplicationAdmission,
    OpenPublicationProfile,
    DesignatedOpenAdmission,
)
from agentclaw.community.core.task.context_collab.l6_products.relation_storage import (
    from_legacy_relation_fields,
)

from ...l5_methods.multi_rollout_policy import (
    MULTI_POLICY_TYPES,
    MultiRolloutMethodPolicy,
    MultiRolloutLimits,
    AdaptiveUntilAccepted,
)

COMPOSITION_TYPES = (
    *MULTI_POLICY_TYPES,
    *WORKFLOW_TYPES,
    AgentRef,
    MethodProfile,
    MethodTemplate,
    MethodRelationTemplate,
    MethodCompositionTemplate,
    SwarmStagesPolicy,
    SwarmWorkflowPolicy,
    SwarmMethodPolicy,
    OpenMethodPolicy,
    OpenAdmissionPolicy,
    BotSelfApplicationAdmission,
    OpenPublicationProfile,
    DesignatedOpenAdmission,
)


def default_composition() -> MethodCompositionTemplate:
    """Product-owned interactive iteration over the static YAML workflow.

    References identify required host capabilities, not concrete implementations.
    The host must install them; missing capabilities still fail closed.
    """
    return MethodCompositionTemplate(
        required_methods=(
            MethodTemplate(
                get_profile("MultiRollout", "1.0"),
                MultiRolloutMethodPolicy(
                    strategy=AdaptiveUntilAccepted(
                        feedback_mode="interactive",
                        adaptation_ref="stage-improvement-agent@1",
                        feedback_timeout_seconds=600,
                    ),
                    limits=MultiRolloutLimits(
                        max_rollouts=4,
                        max_elapsed_seconds=3600,
                        max_stage_dispatches=400,
                        max_strategy_calls=8,
                        max_no_progress=2,
                    ),
                    isolation_ref="per-candidate-workspace@1",
                ),
            ),
            MethodTemplate(
                get_profile("Swarm", "0.2"),
                SwarmStagesPolicy(
                    execution_ref="openapi-bot/v1",
                    acceptance_ref="trusted-evidence/v1",
                    overall_acceptance="stages",
                ),
            ),
        ),
        relations=(
            MethodRelationTemplate(
                source_method="MultiRollout",
                target_method="Swarm",
                relation=CompositionRelation.NEST,
            ),
        ),
    )


def encode_composition(value: MethodCompositionTemplate) -> dict:
    return encode(value)


def decode_composition(data: dict) -> MethodCompositionTemplate:
    return decode(data, MethodCompositionTemplate, COMPOSITION_TYPES, get_profile)


def compile_configuration(composition, stages):
    if type(composition) is not MethodCompositionTemplate:
        raise Invalid("composition is required")
    required, optional = composition.required_methods, composition.optional_methods
    for declarations in (required, optional):
        if type(declarations) is not tuple or any(
            type(d) is not MethodTemplate for d in declarations
        ):
            raise Invalid("Methods must be immutable typed declarations")
        for declaration in declarations:
            profile, policy = declaration.profile, declaration.policy
            if type(profile) is not MethodProfile or profile != get_profile(
                profile.method_kind, profile.profile_version
            ):
                raise Invalid("MethodProfile is not an installed exact contract")
            if policy is not None and (
                type(policy)
                not in {
                    MultiRolloutMethodPolicy,
                    SwarmStagesPolicy,
                    SwarmWorkflowPolicy,
                    OpenMethodPolicy,
                    SwarmMethodPolicy,
                }
                or policy.method != profile.method_kind
            ):
                raise Invalid("Method policy does not match its Profile")
    kinds = [d.profile.method_kind for d in required]
    optional_kinds = [d.profile.method_kind for d in optional]
    if (
        not kinds
        or len(set(kinds)) != len(kinds)
        or len(set(optional_kinds)) != len(optional_kinds)
        or set(kinds) & set(optional_kinds)
    ):
        raise Invalid("empty or duplicate Method selection")
    if type(composition.relations) is not tuple:
        raise Invalid("Method relations must be an immutable tuple")
    pairs = []
    for relation in composition.relations:
        if (
            type(relation) is not MethodRelationTemplate
            or type(relation.relation) is not CompositionRelation
        ):
            raise Invalid("invalid Method relation")
        if relation.source_method not in kinds or relation.target_method not in kinds:
            raise Invalid("relation endpoint is not a selected Method")
        validate_composition(
            relation.source_method, relation.relation, relation.target_method
        )
        pairs.append((relation.source_method, relation.target_method))
    if len(set(pairs)) != len(pairs) or detect_cycle(pairs):
        raise Invalid("duplicate or cyclic Method relations")
    if len(kinds) > 1 and {k for pair in pairs for k in pair} != set(kinds):
        raise Invalid("selected Methods require explicit connected relations")
    if "MultiRollout" in kinds:
        roots = [d for d in required if d.profile.method_kind == "MultiRollout"]
        outgoing = [
            r for r in composition.relations if r.source_method == "MultiRollout"
        ]
        if (
            type(roots[0].policy) is not MultiRolloutMethodPolicy
            or len(outgoing) != 1
            or outgoing[0].relation is not CompositionRelation.NEST
            or outgoing[0].target_method != "Swarm"
            or set(kinds) != {"MultiRollout", "Swarm"}
            or len(composition.relations) != 1
        ):
            raise Invalid(
                "MultiRollout requires exactly one executable NEST Swarm body"
            )
    specs = compile_stages(stages)
    # Never silently choose YAML over an independently writable legacy DAG.
    if any(isinstance(d.policy, SwarmWorkflowPolicy) for d in (*required, *optional)):
        raise Invalid(
            "legacy workflow graph requires explicit migration to YAML-owned stages"
        )
    policies = [d.policy for d in required if type(d.policy) is SwarmStagesPolicy]
    if len(policies) != 1:
        raise Invalid("YAML stages require exactly one selected SwarmStagesPolicy")
    # Open admission/Single/Group execution need their own governed adapters.
    # A binding or eligibility declaration alone is never authority to dispatch.
    return specs, policies[0]


def validate_configuration(composition, stages, execution):
    specs, policy = compile_configuration(composition, stages)
    execution.require(policy.execution_ref, policy.acceptance_ref, specs)
    return specs, policy


def encode_profile(profile: ProductProfile) -> dict:
    return {
        "id": profile.id,
        "name": profile.name,
        "composition": encode_composition(profile.composition),
        "stages": profile.stages,
    }


def decode_profile(data: dict) -> ProductProfile:
    shape(data, {"id", "name", "composition", "stages"})
    return ProductProfile(
        data["id"],
        data["name"],
        decode_composition(from_legacy_relation_fields(data["composition"])),
        data["stages"],
    )


def multi_policy(composition):
    return next(
        (
            d.policy
            for d in composition.required_methods
            if d.profile.method_kind == "MultiRollout"
        ),
        None,
    )


def encode_profile_response(record: "ProductProfileRecord") -> dict:
    """Metadata is intentionally excluded from the frozen execution encoding."""
    return {
        **encode_profile(record.definition),
        "description": record.description,
        "status": record.status.value,
        "creator": record.creator,
        "modifer": record.modifer,
    }
