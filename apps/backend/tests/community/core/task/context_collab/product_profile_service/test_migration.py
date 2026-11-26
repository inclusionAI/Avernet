from dataclasses import replace
from hashlib import sha256
import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmWorkflowPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow import (
    SwarmWorkflowDefinition,
    SwarmWorkflowRef,
    SwarmWorkflowNode,
    SwarmArtifactOutput,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    validate_configuration,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.migration import (
    extract_workflow_composition,
    LegacyProfileMapping,
    validate_mapping,
    require_recoverable_history,
)


def legacy_composition(h):
    output = SwarmArtifactOutput("result", "a result", "document", "doc/v1")
    nodes = tuple(
        SwarmWorkflowNode(
            node_id=name,
            name=name,
            objective="produce work",
            agents=(AgentRef("bot", "worker-a"),),
            input_contract_ref="input/v1",
            output_contract_ref="output/v1",
            completion_contract_ref=f"{name}/v1",
            outputs=(output,),
            depends_on=deps,
        )
        for name, deps in [("analysis", ()), ("implementation", ("analysis",))]
    )
    legacy = SwarmWorkflowPolicy(
        SwarmWorkflowDefinition(SwarmWorkflowRef("legacy-dag", 1), nodes)
    )
    return replace(
        h.composition,
        required_methods=(replace(h.composition.required_methods[0], policy=legacy),),
    )


def test_legacy_graph_cannot_compete_with_yaml(runtime):
    with pytest.raises(Invalid, match="explicit migration"):
        validate_configuration(
            legacy_composition(runtime), runtime.stages, runtime.execution
        )


def test_explicit_graph_extraction_requires_matching_condition_mapping(runtime):
    h = runtime
    original = legacy_composition(h)
    conditions = {
        "analysis/v1": ("design is complete",),
        "implementation/v1": ("tests pass",),
    }
    result = extract_workflow_composition(
        original, h.stages, h.composition.required_methods[0].policy, conditions
    )
    assert result == h.composition
    with pytest.raises(Invalid, match="conflict"):
        extract_workflow_composition(
            original,
            h.stages.replace("depends_on: [analysis]", "depends_on: []"),
            h.composition.required_methods[0].policy,
            conditions,
        )
    with pytest.raises(Invalid, match="conflict"):
        extract_workflow_composition(
            original, h.stages, h.composition.required_methods[0].policy, {}
        )
    assert type(original.required_methods[0].policy) is SwarmWorkflowPolicy


def test_source_mapping_uses_exact_version_digest_and_allocated_identity():
    source = '{"legacy":"unchanged"}'
    mapping = LegacyProfileMapping(
        VersionedProfileRef("legacy", 2), sha256(source.encode()).hexdigest(), 42
    )
    validate_mapping(mapping, source)
    with pytest.raises(Invalid):
        validate_mapping(replace(mapping, target_id=True), source)
    with pytest.raises(Invalid):
        validate_mapping(mapping, source + " ")


def test_sql_tasks_without_governance_are_not_runtime_recovery():
    with pytest.raises(Invalid, match="complete trusted"):
        require_recoverable_history({"child_tasks": ["legacy-task-1"]})
