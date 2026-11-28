"""Bounded strict YAML compiler. Compiled stages are not a configuration writer."""

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    text,
    shape,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    CompiledStage,
)


class StrictLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            raise Invalid("YAML aliases are not supported")
        self.depth = getattr(self, "depth", 0) + 1
        if self.depth > 20:
            raise Invalid("YAML nesting limit exceeded")
        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, MappingNode):
            raise Invalid("YAML mapping required")
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if type(key) is not str or key in result:
                raise Invalid("YAML keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def compile_stages(value: str) -> tuple[CompiledStage, ...]:
    text(value, maximum=256 * 1024)
    try:
        data = yaml.load(value, Loader=StrictLoader)
    except (yaml.YAMLError, RecursionError, TypeError, ValueError):
        # YAML exceptions can contain inline source snippets; never log them.
        raise Invalid("invalid stages YAML") from None
    shape(data, {"stages"})
    nodes = data["stages"]
    if type(nodes) is not list or not 0 < len(nodes) <= 100:
        raise Invalid("stages must contain 1..100 nodes")
    result = []
    for node in nodes:
        shape(node, {"name", "candidates", "depends_on", "acceptance"})
        text(node["name"], maximum=256)
        candidates, dependencies, acceptance = (
            node["candidates"],
            node["depends_on"],
            node["acceptance"],
        )
        if type(candidates) is not list or not 0 < len(candidates) <= 100:
            raise Invalid("stage requires bounded candidates")
        agents = []
        for item in candidates:
            shape(item, {"type", "uuid"})
            text(item["type"], maximum=64)
            text(item["uuid"], maximum=256)
            if item["type"] not in {"bot", "human", "service"}:
                raise Invalid("unknown candidate type")
            agents.append(AgentRef(item["type"], item["uuid"]))
        if len(set(agents)) != len(agents):
            raise Invalid("duplicate candidate")
        if type(dependencies) is not list or len(dependencies) > 100:
            raise Invalid("depends_on must be a bounded list")
        for dep in dependencies:
            text(dep, maximum=256)
        if len(set(dependencies)) != len(dependencies):
            raise Invalid("duplicate dependency")
        if type(acceptance) is dict:
            acceptance = [acceptance]
        if type(acceptance) is not list or not 0 < len(acceptance) <= 100:
            raise Invalid("acceptance requires conditions")
        conditions = []
        for item in acceptance:
            shape(item, {"condition"})
            text(item["condition"], maximum=4096)
            conditions.append(item["condition"])
        if len(set(conditions)) != len(conditions):
            raise Invalid("duplicate acceptance condition")
        result.append(
            CompiledStage(
                node["name"], tuple(agents), tuple(dependencies), tuple(conditions)
            )
        )
    names = {s.name for s in result}
    if len(names) != len(result):
        raise Invalid("duplicate stage name")
    pending = {s.name: set(s.depends_on) for s in result}
    if any(not deps <= names or name in deps for name, deps in pending.items()):
        raise Invalid("unknown or self dependency")
    done = set()
    while pending:
        ready = {name for name, deps in pending.items() if deps <= done}
        if not ready:
            raise Invalid("cyclic stage dependencies")
        done |= ready
        pending = {name: deps for name, deps in pending.items() if name not in ready}
    return tuple(result)
