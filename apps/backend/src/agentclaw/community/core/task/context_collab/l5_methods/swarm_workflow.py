"""Immutable, bounded Swarm DAG authoring values; not the root Task workflow."""

from dataclasses import dataclass
from hashlib import sha256
import json

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.value_codec import (
    decode,
    encode,
)


def _strings(*values):
    if any(type(v) is not str or not v.strip() for v in values):
        raise ContextCollabValidationError("workflow requires non-empty strings")


def _tuple(value, item_type, limit, *, nonempty=False):
    if (
        type(value) is not tuple
        or len(value) > limit
        or (nonempty and not value)
        or any(not isinstance(v, item_type) for v in value)
    ):
        raise ContextCollabValidationError("invalid/bounds-exceeded workflow tuple")


@dataclass(frozen=True)
class SwarmWorkflowRef:
    workflow_id: str
    version: int

    def __post_init__(self) -> None:
        _strings(self.workflow_id)
        if type(self.version) is not int or not 1 <= self.version <= 2147483647:
            raise ContextCollabValidationError("invalid workflow version")


@dataclass(frozen=True)
class SwarmArtifactOutput:
    name: str
    description: str
    resource_type: str
    content_contract_ref: str
    min_items: int = 1
    max_items: int = 1

    def __post_init__(self) -> None:
        _strings(
            self.name, self.description, self.resource_type, self.content_contract_ref
        )
        if (
            type(self.min_items) is not int
            or type(self.max_items) is not int
            or not 1 <= self.min_items <= self.max_items <= 64
        ):
            raise ContextCollabValidationError("invalid output cardinality")


@dataclass(frozen=True)
class SwarmArtifactInput:
    name: str
    from_node: str
    from_output: str

    def __post_init__(self) -> None:
        _strings(self.name, self.from_node, self.from_output)


@dataclass(frozen=True)
class SwarmWorkflowNode:
    node_id: str
    name: str
    objective: str
    agents: tuple[AgentRef, ...]
    input_contract_ref: str
    output_contract_ref: str
    completion_contract_ref: str
    outputs: tuple[SwarmArtifactOutput, ...]
    inputs: tuple[SwarmArtifactInput, ...] = ()
    depends_on: tuple[str, ...] = ()
    input_artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _strings(
            self.node_id,
            self.name,
            self.objective,
            self.input_contract_ref,
            self.output_contract_ref,
            self.completion_contract_ref,
        )
        _tuple(self.agents, AgentRef, 256, nonempty=True)
        _tuple(self.outputs, SwarmArtifactOutput, 16, nonempty=True)
        _tuple(self.inputs, SwarmArtifactInput, 64)
        _tuple(self.depends_on, str, 256)
        _tuple(self.input_artifact_refs, str, 64)
        for agent in self.agents:
            AgentRef.from_dict(agent.to_dict())
        for values in (
            self.agents,
            tuple(v.name for v in self.outputs),
            tuple(v.name for v in self.inputs),
            self.depends_on,
            self.input_artifact_refs,
        ):
            if len(set(values)) != len(values):
                raise ContextCollabValidationError("duplicate workflow entry")
        _strings(*self.depends_on, *self.input_artifact_refs)


@dataclass(frozen=True)
class SwarmWorkflowDefinition:
    ref: SwarmWorkflowRef
    nodes: tuple[SwarmWorkflowNode, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.ref, SwarmWorkflowRef):
            raise ContextCollabValidationError("workflow requires typed ref")
        _tuple(self.nodes, SwarmWorkflowNode, 256, nonempty=True)
        by_id = {n.node_id: n for n in self.nodes}
        if (
            len(by_id) != len(self.nodes)
            or sum(len(n.depends_on) for n in self.nodes) > 4096
        ):
            raise ContextCollabValidationError("duplicate nodes or too many edges")
        for node in self.nodes:
            if (
                node.node_id in node.depends_on
                or not set(node.depends_on) <= by_id.keys()
            ):
                raise ContextCollabValidationError("invalid workflow dependency")
            for port in node.inputs:
                if port.from_node not in node.depends_on or port.from_output not in {
                    p.name for p in by_id[port.from_node].outputs
                }:
                    raise ContextCollabValidationError(
                        "input must reference a named output of a direct predecessor"
                    )
        resolved = set()
        while len(resolved) < len(by_id):
            ready = {
                n.node_id
                for n in self.nodes
                if n.node_id not in resolved and set(n.depends_on) <= resolved
            }
            if not ready:
                raise ContextCollabValidationError("workflow DAG contains a cycle")
            resolved.update(ready)

    def to_dict(self) -> dict:
        return encode(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SwarmWorkflowDefinition":
        return decode(data, cls, WORKFLOW_TYPES)

    @property
    def digest(self) -> str:
        return sha256(
            json.dumps(
                self.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()


WORKFLOW_TYPES = (
    SwarmWorkflowRef,
    SwarmArtifactOutput,
    SwarmArtifactInput,
    SwarmWorkflowNode,
    SwarmWorkflowDefinition,
    AgentRef,
)
