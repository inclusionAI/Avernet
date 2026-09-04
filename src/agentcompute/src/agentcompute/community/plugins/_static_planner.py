"""Static planner plugin: LLM-backed single-pass planner.

Concrete implementation of ``spi.Planner``. The business logic lives
here (next to other concrete plugins like ``StubLLMProvider`` and
``LLMBackedAgent``); ``core/_planner.py`` is now a backward-compat
shim that re-exports from this module.

Populates ``plan.goal`` and ``plan.available_agents`` on the returned
plan so dynamic replanners have authority-stamped fields.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agentcompute.community.bootstrap import get_container

from .._logger import get_logger
from ..core._dag import DAGPlan
from ..spi._agent import AgentSpec
from ..spi._planner import PlanError
from ..spi._planner import Planner as _PlannerSPI

__all__ = ["StaticPlanner"]

_MAX_RETRIES = 3

logger = get_logger("planner")


class StaticPlanner(_PlannerSPI):
    """Deterministic single-pass LLM planner: goal + agents → DAG.

    Retried up to ``_MAX_RETRIES`` times; raises ``PlanError`` on
    persistent failure. Sets ``plan.goal`` and ``plan.available_agents``
    on the returned plan (SPI contract). No replanning.
    """

    def plan(self, goal: str, agents: list[AgentSpec]) -> DAGPlan:
        names = [a.name for a in agents]
        prompt = self._prompt(goal, names)
        provider = get_container().plugins().llm_provider()
        logger.info("planning goal=%r agents=%s", goal, names)
        logger.debug("planner prompt=%s", prompt)
        stream = getattr(provider, "stream", None)
        last_error: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                if stream is not None:
                    parts: list[str] = []
                    for chunk in stream(prompt, kind="planner"):
                        parts.append(chunk)
                        logger.debug("planner chunk=%r", chunk)
                    raw = "".join(parts)
                    logger.info(
                        "planner attempt=%d stream done chunks=%d bytes=%d",
                        attempt, len(parts), len(raw),
                    )
                else:
                    raw = provider.complete(prompt, kind="planner")
                    logger.info(
                        "planner attempt=%d complete bytes=%d",
                        attempt, len(raw),
                    )
                data = self._extract_json(raw)
                if isinstance(data, dict) and "nodes" in data and "edges" in data:
                    plan_json = self._normalize(data, names)
                    logger.info(
                        "planner attempt=%d plan=%s",
                        attempt,
                        json.dumps(plan_json, ensure_ascii=False),
                    )
                    plan = DAGPlan.from_dict(plan_json)
                    plan.prompt = prompt
                    plan.goal = goal
                    plan.available_agents = list(names)
                    plan.validate()
                    logger.info(
                        "plan produced: %d node(s), %d edge(s)",
                        len(plan.nodes),
                        len(plan.edges),
                    )
                    return plan
                last_error = ValueError(f"planner output missing nodes/edges (attempt {attempt})")
            except (json.JSONDecodeError, TypeError, KeyError, ValueError, AttributeError) as exc:
                last_error = exc
                logger.warning("planner attempt=%d failed: %s", attempt, exc)
        logger.error("planner failed after %d attempt(s): %s", _MAX_RETRIES, last_error)
        raise PlanError(f"planner failed to produce a valid JSON DAG: {last_error}")

    @staticmethod
    def _extract_json(raw: str) -> Any:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            if start == -1:
                raise
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        return json.loads(text[start : i + 1])
            raise

    @staticmethod
    def _prompt(goal: str, names: list[str]) -> str:
        return (
            "You are a task planner. Decompose the goal into several distinct "
            "sub-goals and arrange them as a DIAMOND-shaped DAG to maximize "
            "parallelism: a fan-out of independent per-entity subtasks feeding "
            "a single fan-in merge node. When the goal names multiple entities "
            "(OSes, products, files, components), create ONE separate subtask "
            "per entity so they run concurrently. Assign each subtask to the "
            "most appropriate agent from the candidate list. Every sibling "
            "subtask depends only on the same shared root (or nothing), and "
            "every subtask feeds the merge node. Order pairs as a DAG: a node "
            "lists the nodes it depends on. Respond with JSON only in this "
            "exact shape: "
            '{"nodes": {"<id>": {"agent": "<name>", "input": {"goal": "<sub-goal>"}}}, '
            '"edges": [["<depends-on>", "<node>"]]}. '
            "Use short plain ids (1, 2, 3...) for nodes. Give each node a "
            f"distinct concrete sub-goal. goal={goal!r} agents={names!r}"
        )

    @staticmethod
    def _normalize(data: dict[str, Any], names: list[str]) -> dict[str, Any]:
        nodes = data.get("nodes", {})
        if isinstance(nodes, dict) and all(
            isinstance(value, dict) and "agent" in value for value in nodes.values()
        ):
            return {"nodes": nodes, "edges": data.get("edges", [])}
        return {
            "nodes": {
                str(node_id): {"agent": names[0], "input": {"goal": str(value)}}
                for node_id, value in nodes.items()
            },
            "edges": [
                [str(edge.get("source")), str(edge.get("target"))]
                for edge in data.get("edges", [])
                if isinstance(edge, dict)
            ],
        }