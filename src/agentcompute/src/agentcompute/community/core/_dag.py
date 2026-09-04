"""DAG plan model: nodes + edges, plus JSON serialization."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ._node import DAGNode, NodeStatus

__all__ = ["DAGCycleError", "DAGDanglingEdgeError", "DAGNode", "DAGPlan", "NodeStatus"]


class DAGCycleError(ValueError):
    """Raised when a DAG contains a cycle."""


class DAGDanglingEdgeError(ValueError):
    """Raised when an edge references a node id that does not exist."""


@dataclass
class DAGPlan:
    """A directed acyclic plan of nodes and edges (dependencies).

    ``goal`` and ``available_agents`` are populated by the planner so
    downstream consumers (including dynamic replanners) have access
    without needing the original arguments re-passed to ``Driver.run()``.

    ``extension_history`` preserves every replanner extension applied
    during execution (in order) so the final report can show the full
    plan evolution. Each entry is a JSON-serializable dict with keys:
    ``wave_index``, ``applied_at``, ``rationale``, ``halt_reason``,
    ``nodes_added`` (list of node dicts), ``edges_added`` (list of pairs).
    """

    nodes: dict[str, DAGNode] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    prompt: str = ""
    goal: str = ""
    available_agents: list[str] = field(default_factory=list)
    extension_history: list[dict[str, Any]] = field(default_factory=list)

    def add_node(self, node: DAGNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, src: str, dst: str) -> None:
        self.edges.append((src, dst))

    def dependencies_of(self, node_id: str) -> list[str]:
        return [s for s, d in self.edges if d == node_id]

    def dependents_of(self, node_id: str) -> list[str]:
        return [d for s, d in self.edges if s == node_id]

    def validate(self) -> None:
        for src, dst in self.edges:
            if src not in self.nodes:
                raise DAGDanglingEdgeError(f"edge source '{src}' not in nodes")
            if dst not in self.nodes:
                raise DAGDanglingEdgeError(f"edge target '{dst}' not in nodes")
        if self._has_cycle():
            raise DAGCycleError("DAG contains a cycle")

    def topological_order(self) -> list[str]:
        self.validate()
        return self._kahn()

    def _has_cycle(self) -> bool:
        return len(self._kahn()) != len(self.nodes)

    def _kahn(self) -> list[str]:
        indegree: dict[str, int] = {n: 0 for n in self.nodes}
        for _src, dst in self.edges:
            indegree[dst] += 1
        ready = sorted(n for n, d in indegree.items() if d == 0)
        order: list[str] = []
        while ready:
            node = ready.pop(0)
            order.append(node)
            for dep in self.dependents_of(node):
                indegree[dep] -= 1
                if indegree[dep] == 0:
                    ready.append(dep)
        return order

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": [list(e) for e in self.edges],
            "prompt": self.prompt,
            "goal": self.goal,
            "available_agents": list(self.available_agents),
            "extension_history": [dict(rec) for rec in self.extension_history],
        }

    def to_full_dict(self) -> dict[str, Any]:
        return {
            "nodes": {nid: n.to_full_dict() for nid, n in self.nodes.items()},
            "edges": [list(e) for e in self.edges],
            "prompt": self.prompt,
            "goal": self.goal,
            "available_agents": list(self.available_agents),
            "extension_history": [dict(rec) for rec in self.extension_history],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DAGPlan:
        plan = cls(
            prompt=data.get("prompt", ""),
            goal=data.get("goal", ""),
            available_agents=list(data.get("available_agents", [])),
            extension_history=[
                dict(rec) for rec in data.get("extension_history", [])
            ],
        )
        for nid, node_data in data.get("nodes", {}).items():
            plan.add_node(DAGNode.from_dict(nid, node_data))
        for src, dst in data.get("edges", []):
            plan.add_edge(src, dst)
        return plan

    @classmethod
    def from_full_dict(cls, data: dict[str, Any]) -> DAGPlan:
        plan = cls(
            prompt=data.get("prompt", ""),
            goal=data.get("goal", ""),
            available_agents=list(data.get("available_agents", [])),
            extension_history=[
                dict(rec) for rec in data.get("extension_history", [])
            ],
        )
        for nid, node_data in data.get("nodes", {}).items():
            node_plan = DAGNode.from_dict(nid, node_data)
            node_plan.status = NodeStatus(node_data.get("status", node_plan.status.value))
            node_plan.result = node_data.get("result")
            node_plan.error = node_data.get("error")
            node_plan.started_at = node_data.get("started_at")
            node_plan.finished_at = node_data.get("finished_at")
            node_plan.wave = node_data.get("wave")
            node_plan.plan_round = node_data.get("plan_round", 0)
            plan.add_node(node_plan)
        for src, dst in data.get("edges", []):
            plan.add_edge(src, dst)
        return plan

    @classmethod
    def from_json(cls, raw: str) -> DAGPlan:
        return cls.from_dict(json.loads(raw))
