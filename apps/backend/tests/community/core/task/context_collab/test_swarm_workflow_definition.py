from dataclasses import replace

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow import (
    SwarmWorkflowDefinition,
    SwarmWorkflowRef,
    SwarmArtifactOutput,
    SwarmArtifactInput,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from tests.community.core.task.context_collab.swarm_workflow_support import (
    workflow,
    node,
    profile,
)


def test_workflow_roundtrip_and_order_sensitive_digest():
    graph = workflow()
    assert SwarmWorkflowDefinition.from_dict(graph.to_dict()) == graph
    assert replace(graph, nodes=tuple(reversed(graph.nodes))).digest != graph.digest
    assert ProductCatalog((profile(),)).get("workflow_delivery", 1) == profile()


@pytest.mark.parametrize(
    "bad_nodes",
    [
        (),
        (node("a"), node("a")),
        (node("a", ("a",)),),
        (node("a", ("missing",)),),
        (node("a", ("b",)), node("b", ("a",))),
        (node("a"), node("b", (), (SwarmArtifactInput("x", "a", "result"),))),
        (node("a"), node("b", ("a",), (SwarmArtifactInput("x", "a", "missing"),))),
    ],
)
def test_invalid_dag_fails_at_authoring(bad_nodes):
    with pytest.raises(ContextCollabValidationError):
        SwarmWorkflowDefinition(SwarmWorkflowRef("bad", 1), bad_nodes)


@pytest.mark.parametrize(
    "field,value",
    [
        ("agents", ()),
        ("agents", (AgentRef("bot", "x"),) * 2),
        ("outputs", ()),
        ("inputs", (SwarmArtifactInput("x", "a", "result"),) * 2),
        ("objective", ""),
    ],
)
def test_invalid_node_contract(field, value):
    with pytest.raises(ContextCollabValidationError):
        replace(node("a"), **{field: value})


@pytest.mark.parametrize("low,high", [(0, 1), (2, 1), (True, 1), (1, 65)])
def test_output_cardinality_is_bounded(low, high):
    with pytest.raises(ContextCollabValidationError):
        SwarmArtifactOutput("out", "desc", "document", "text/1", low, high)


def test_unknown_fields_and_agent_scope_rejected():
    graph = workflow().to_dict()
    graph["nodes"][0]["unexpected"] = 1
    with pytest.raises(ContextCollabValidationError):
        SwarmWorkflowDefinition.from_dict(graph)
    with pytest.raises(ContextCollabValidationError):
        ProductCatalog(
            (
                profile(
                    SwarmWorkflowDefinition(
                        SwarmWorkflowRef("x", 1),
                        (node("a", agents=(AgentRef("bot", "outsider"),)),),
                    )
                ),
            )
        )


def test_graph_boundaries():
    valid = tuple(node(str(i)) for i in range(256))
    assert len(SwarmWorkflowDefinition(SwarmWorkflowRef("max", 1), valid).nodes) == 256
    with pytest.raises(ContextCollabValidationError):
        SwarmWorkflowDefinition(SwarmWorkflowRef("over", 1), valid + (node("extra"),))
