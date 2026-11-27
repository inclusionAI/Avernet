"""Explicit, fail-closed preparation of legacy DAGs; never guesses lost history."""

from dataclasses import dataclass, replace
from hashlib import sha256
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmWorkflowPolicy,
    SwarmStagesPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodCompositionTemplate,
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.stages_yaml import (
    compile_stages,
)


@dataclass(frozen=True)
class LegacyProfileMapping:
    """Exact source/version → repository-assigned integer identity (never a hash ID)."""

    source: VersionedProfileRef
    source_digest: str
    target_id: int


def extract_workflow_composition(
    composition: MethodCompositionTemplate,
    stages: str,
    policy: SwarmStagesPolicy,
    acceptance_contracts: dict[str, tuple[str, ...]],
) -> MethodCompositionTemplate:
    """Prepare an explicitly reviewed graph replacement, retaining all other Methods.

    Completion contract references need explicit condition mappings; output/I/O,
    resource, role and governance equivalence still needs deployment approval.
    This function cannot authorize migration or restore an in-flight runtime.
    """
    specs = compile_stages(stages)
    legacy = [
        d for d in composition.required_methods if type(d.policy) is SwarmWorkflowPolicy
    ]
    if len(legacy) != 1 or type(policy) is not SwarmStagesPolicy:
        raise Invalid(
            "migration requires exactly one legacy workflow and an explicit graph-free policy"
        )
    nodes = legacy[0].policy.workflow.nodes
    names = {n.node_id: n.name for n in nodes}
    expected = {
        n.name: (
            tuple(n.agents),
            tuple(names[d] for d in n.depends_on),
            acceptance_contracts.get(n.completion_contract_ref),
        )
        for n in nodes
    }
    actual = {s.name: (s.candidates, s.depends_on, s.conditions) for s in specs}
    if len(expected) != len(nodes) or expected != actual:
        raise Invalid("legacy workflow and YAML DAG/acceptance mappings conflict")
    return replace(
        composition,
        required_methods=tuple(
            replace(d, policy=policy) if d is legacy[0] else d
            for d in composition.required_methods
        ),
    )


def validate_mapping(mapping: LegacyProfileMapping, source_json: str) -> None:
    from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
        positive_id,
    )

    positive_id(mapping.target_id)
    if (
        type(mapping.source) is not VersionedProfileRef
        or mapping.source_digest != sha256(source_json.encode()).hexdigest()
    ):
        raise Invalid(
            "legacy mapping requires exact source identity and content digest"
        )


def require_recoverable_history(snapshot) -> None:
    """SQL child Tasks alone are never proof of recoverable allocations/sessions."""
    from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
        RuntimeSnapshot,
    )
    from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.snapshot_codec import (
        decode_snapshot,
        encode_snapshot,
    )

    if type(snapshot) is not RuntimeSnapshot:
        raise Invalid("legacy execution lacks a complete trusted runtime snapshot")
    decode_snapshot(encode_snapshot(snapshot))
