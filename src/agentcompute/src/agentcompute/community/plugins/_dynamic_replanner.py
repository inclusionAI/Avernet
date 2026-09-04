"""Dynamic replanner: LLM-driven plan extension between execution waves.

Reads prior plan + completed results + errors and asks the LLM to
emit a JSON delta (new nodes + edges, optional halt_reason). Safety
caps (``max_extensions``, ``max_total_nodes``, ``replan_timeout_seconds``,
``max_total_injected_chars``) prevent runaway replanning.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from typing import Any

from agentcompute.community.bootstrap import get_container

from .._logger import get_logger
from ..core._dag import DAGPlan
from ..core._node import DAGNode, NodeStatus
from ..spi._replanner import (
    HaltReason,
    PlanExtension,
    Replanner,
)

__all__ = ["DynamicReplanner"]

logger = get_logger("replanner")


class DynamicReplanner(Replanner):
    def __init__(
        self,
        max_extensions: int = 10,
        max_total_nodes: int = 100,
        replan_timeout_seconds: float = 120.0,
        max_total_injected_chars: int = 8000,
        inject_completed_results: bool = True,
        inject_errors: bool = True,
        custom_extension_prompt: str | None = None,
        dedup_strategy: str = "skip_dup_goals",
        force_extension_until_calls: int = 0,
        max_parse_retries: int = 2,
    ) -> None:
        self._max_extensions = max_extensions
        self._max_total_nodes = max_total_nodes
        self._replan_timeout_seconds = replan_timeout_seconds
        self._max_total_injected_chars = max_total_injected_chars
        self._inject_completed_results = inject_completed_results
        self._inject_errors = inject_errors
        self._custom_extension_prompt = custom_extension_prompt
        self._dedup_strategy = dedup_strategy
        self._force_extension_until_calls = max(0, force_extension_until_calls)
        self._max_parse_retries = max(0, max_parse_retries)
        self._replan_count = 0
        self._total_injected_chars = 0

    def extend(
        self,
        goal: str,
        agents: list[str],
        prior: DAGPlan,
        results: dict[str, Any],
        errors: dict[str, str],
    ) -> PlanExtension:
        self._replan_count += 1
        done = sum(1 for n in prior.nodes.values() if n.status == NodeStatus.SUCCEEDED)
        failed = sum(1 for n in prior.nodes.values() if n.status == NodeStatus.FAILED)
        pending = sum(1 for n in prior.nodes.values() if n.status == NodeStatus.PENDING)
        logger.info(
            "replanner call=%d goal=%r done=%d failed=%d pending=%d total_nodes=%d",
            self._replan_count, goal, done, failed, pending, len(prior.nodes),
        )
        if self._replan_count > self._max_extensions:
            logger.warning(
                "replanner aborting: replan_count=%d exceeds max_extensions=%d",
                self._replan_count, self._max_extensions,
            )
            return PlanExtension(
                halt_reason=HaltReason.ABORT,
                rationale=f"max_extensions={self._max_extensions} reached",
            )
        if len(prior.nodes) >= self._max_total_nodes:
            logger.warning(
                "replanner aborting: total_nodes=%d exceeds max_total_nodes=%d",
                len(prior.nodes), self._max_total_nodes,
            )
            return PlanExtension(
                halt_reason=HaltReason.ABORT,
                rationale=f"max_total_nodes={self._max_total_nodes} reached",
            )

        extension = self._call_and_parse_with_retries(
            goal, agents, prior, results, errors
        )

        logger.info(
            "replanner call=%d result: new_nodes=%d new_edges=%d halt=%s rationale=%r",
            self._replan_count,
            len(extension.extensions),
            len(extension.new_edges),
            extension.halt_reason,
            extension.rationale,
        )
        return extension

    def _call_and_parse_with_retries(
        self,
        goal: str,
        agents: list[str],
        prior: DAGPlan,
        results: dict[str, Any],
        errors: dict[str, str],
    ) -> PlanExtension:
        """Call LLM + parse JSON, retrying with less context on truncation.

        LLM responses can be truncated mid-JSON (token cap, early stop).
        Retry by stripping completed results → errors → halving char budget.
        On final failure return a no-op extension instead of raising, so
        the driver continues to the next wave.
        """
        inject_results = self._inject_completed_results
        inject_errors = self._inject_errors
        inject_chars = self._max_total_injected_chars
        last_exc: Exception | None = None

        for attempt in range(self._max_parse_retries + 1):
            prompt = self._build_prompt(
                goal, agents, prior, results, errors,
                inject_results=inject_results,
                inject_errors=inject_errors,
                max_injected_chars=inject_chars,
            )
            try:
                raw = self._call_llm_with_timeout(prompt)
            except TimeoutError as exc:
                logger.warning(
                    "replanner LLM timed out after %ss (attempt %d/%d)",
                    self._replan_timeout_seconds, attempt + 1,
                    self._max_parse_retries + 1,
                )
                last_exc = exc
                break
            except Exception as exc:
                logger.warning(
                    "replanner LLM call failed: %s (attempt %d/%d)",
                    exc, attempt + 1, self._max_parse_retries + 1,
                )
                last_exc = exc
                break

            try:
                return self._parse_extension(raw, prior)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                last_exc = exc
                logger.warning(
                    "replanner output invalid (attempt %d/%d): %s | raw_len=%d",
                    attempt + 1, self._max_parse_retries + 1, exc, len(raw),
                )
                if attempt < self._max_parse_retries:
                    if inject_results:
                        inject_results = False
                        logger.info("replanner retry: stripping completed results from prompt")
                    elif inject_errors:
                        inject_errors = False
                        logger.info("replanner retry: stripping failed errors from prompt")
                    else:
                        inject_chars = max(500, inject_chars // 2)
                        logger.info(
                            "replanner retry: halving injected chars to %d", inject_chars,
                        )

        logger.warning(
            "replanner returning no-op after %d failed attempt(s): %s",
            self._max_parse_retries + 1, last_exc,
        )
        return PlanExtension(
            extensions=[],
            new_edges=[],
            halt_reason=None,
            rationale=f"replanner output invalid after {self._max_parse_retries + 1} attempt(s)",
        )

    def _build_prompt(
        self,
        goal: str,
        agents: list[str],
        prior: DAGPlan,
        results: dict[str, Any],
        errors: dict[str, str],
        inject_results: bool | None = None,
        inject_errors: bool | None = None,
        max_injected_chars: int | None = None,
    ) -> str:
        use_results = (
            inject_results if inject_results is not None else self._inject_completed_results
        )
        use_errors = (
            inject_errors if inject_errors is not None else self._inject_errors
        )
        char_limit = (
            max_injected_chars if max_injected_chars is not None
            else self._max_total_injected_chars
        )
        completed = [
            {
                "id": nid,
                "agent": prior.nodes[nid].agent,
                "goal": str(prior.nodes[nid].input.get("goal", "")),
                "result": _truncate(str(results.get(nid, "")), char_limit),
            }
            for nid, n in prior.nodes.items()
            if use_results and n.status == NodeStatus.SUCCEEDED
        ]
        failed = [
            {"id": nid, "agent": prior.nodes[nid].agent, "error": errors.get(nid, "")}
            for nid, n in prior.nodes.items()
            if use_errors and n.status == NodeStatus.FAILED
        ]
        pending = [
            {"id": nid, "agent": prior.nodes[nid].agent, "goal": str(prior.nodes[nid].input.get("goal", ""))}
            for nid, n in prior.nodes.items()
            if n.status == NodeStatus.PENDING
        ]
        prior_summary = {
            "nodes_done": len(completed),
            "nodes_failed": len(failed),
            "nodes_pending": len(pending),
            "edges": [list(e) for e in prior.edges],
        }
        base = self._custom_extension_prompt or _DEFAULT_EXTENSION_PROMPT
        prompt = base.format(
            goal=goal,
            agents=agents,
            completed=completed,
            failed=failed,
            pending=pending,
            prior_summary=prior_summary,
        )
        if self._replan_count <= self._force_extension_until_calls:
            prompt += _FORCE_EXTENSION_SUFFIX
        return prompt

    def _call_llm_with_timeout(self, prompt: str) -> str:
        provider = get_container().plugins().llm_provider()
        result_holder: dict[str, Any] = {}

        def _worker() -> None:
            try:
                stream = getattr(provider, "stream", None)
                if stream is not None:
                    parts: list[str] = []
                    for chunk in stream(prompt, kind="replanner"):
                        parts.append(chunk)
                    result_holder["value"] = "".join(parts)
                    result_holder["chunks"] = len(parts)
                else:
                    result_holder["value"] = provider.complete(prompt, kind="replanner")
                    result_holder["chunks"] = None
            except Exception as exc:
                result_holder["error"] = exc

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        thread.join(timeout=self._replan_timeout_seconds)
        if thread.is_alive():
            raise TimeoutError("LLM call exceeded timeout")
        if "error" in result_holder:
            raise result_holder["error"]
        value = str(result_holder.get("value", ""))
        chunks = result_holder.get("chunks")
        if chunks is None:
            logger.info("replanner LLM complete bytes=%d", len(value))
        else:
            logger.info("replanner LLM stream done chunks=%d bytes=%d", chunks, len(value))
        return value

    def _parse_extension(self, raw: str, prior: DAGPlan) -> PlanExtension:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            text = text.strip()
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("replanner output is not a JSON object")

        halt_raw = data.get("halt_reason")
        halt_reason: HaltReason | None = None
        if halt_raw:
            try:
                halt_reason = HaltReason(str(halt_raw).lower())
            except ValueError as exc:
                raise ValueError(f"halt_reason='{halt_raw}' must be one of {list(HaltReason)}") from exc

        existing_ids = set(prior.nodes.keys())
        existing_goals = {
            str(n.input.get("goal", "")).strip().lower()
            for n in prior.nodes.values()
        }
        existing_edges = set(prior.edges)

        new_nodes: list[DAGNode] = []
        nodes_payload = data.get("nodes", {})
        if isinstance(nodes_payload, dict):
            for node_id, node_data in nodes_payload.items():
                nid = str(node_id)
                if nid in existing_ids and self._dedup_strategy == "skip_dup_goals":
                    continue
                if not isinstance(node_data, dict):
                    continue
                goal_text = str(node_data.get("input", {}).get("goal", "")).strip().lower()
                if self._dedup_strategy == "skip_dup_goals" and goal_text and goal_text in existing_goals:
                    continue
                new_nodes.append(
                    DAGNode(
                        id=nid,
                        agent=str(node_data.get("agent", "")),
                        input=dict(node_data.get("input", {})),
                    )
                )
        elif isinstance(nodes_payload, list):
            for node_data in nodes_payload:
                if not isinstance(node_data, dict):
                    continue
                nid = str(node_data.get("id", ""))
                if not nid or (nid in existing_ids and self._dedup_strategy == "skip_dup_goals"):
                    continue
                goal_text = str(node_data.get("input", {}).get("goal", "")).strip().lower()
                if self._dedup_strategy == "skip_dup_goals" and goal_text and goal_text in existing_goals:
                    continue
                new_nodes.append(
                    DAGNode(
                        id=nid,
                        agent=str(node_data.get("agent", "")),
                        input=dict(node_data.get("input", {})),
                    )
                )

        new_edges: list[tuple[str, str]] = []
        for edge in data.get("edges", []):
            if isinstance(edge, list) and len(edge) == 2:
                pair = (str(edge[0]), str(edge[1]))
                if pair not in existing_edges:
                    new_edges.append(pair)
            elif isinstance(edge, dict):
                pair = (str(edge.get("source")), str(edge.get("target")))
                if pair not in existing_edges:
                    new_edges.append(pair)

        new_nodes, new_edges = self._continue_id_scheme(new_nodes, new_edges, prior)

        return PlanExtension(
            extensions=new_nodes,
            new_edges=new_edges,
            halt_reason=halt_reason,
            rationale=str(data.get("rationale", "")),
        )

    @staticmethod
    def _continue_id_scheme(
        new_nodes: list[DAGNode],
        new_edges: list[tuple[str, str]],
        prior: DAGPlan,
    ) -> tuple[list[DAGNode], list[tuple[str, str]]]:
        """Renumber post-replan nodes to match the prior plan's ID convention.

        The replanner prompt suggests placeholder IDs like ``r1``, ``r2``; the
        initial planner uses bare integers (1, 2, 3...) or ``P\\d+`` ids. The
        report renders node IDs as-is, so a mixed ``1, 2, 3, 4, r1, r2`` DAG
        looks inconsistent. This helper picks the dominant prefix and max
        integer from ``prior.nodes`` and assigns the next integers in sequence
        to the new nodes, rewriting any new edges that reference them. If no
        prior node ID matches the integer-suffix pattern, the LLM's IDs are
        passed through unchanged.
        """
        if not new_nodes:
            return new_nodes, new_edges
        prefix, next_int = DynamicReplanner._next_id_scheme(prior)
        if prefix is None:
            return new_nodes, new_edges
        id_map: dict[str, str] = {}
        out_nodes: list[DAGNode] = []
        for node in new_nodes:
            new_id = f"{prefix}{next_int}"
            next_int += 1
            id_map[node.id] = new_id
            out_nodes.append(
                DAGNode(id=new_id, agent=node.agent, input=dict(node.input))
            )
        out_edges: list[tuple[str, str]] = [
            (id_map.get(src, src), id_map.get(dst, dst)) for src, dst in new_edges
        ]
        return out_nodes, out_edges

    @staticmethod
    def _next_id_scheme(prior: DAGPlan) -> tuple[str | None, int]:
        """Infer ``(prefix, next_int)`` from the prior plan's node IDs.

        Scans IDs like ``1``, ``5``, ``P3``, ``P7`` and returns the most
        common prefix (empty string for bare integers) plus the next integer
        after the max suffix seen. Returns ``(None, 0)`` when no prior ID
        matches ``^([A-Za-z_]*?)(\\d+)$``, signalling the caller to leave
        the LLM's IDs alone rather than guess a scheme.
        """
        prefix_counts: dict[str, int] = {}
        max_int = 0
        found = False
        for nid in prior.nodes:
            m = re.match(r"^([A-Za-z_]*?)(\d+)$", nid)
            if not m:
                continue
            prefix = m.group(1)
            n = int(m.group(2))
            prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
            if n > max_int:
                max_int = n
            found = True
        if not found:
            return None, 0
        prefix = max(prefix_counts, key=prefix_counts.get)
        return prefix, max_int + 1


_DEFAULT_EXTENSION_PROMPT = (
    "You are a plan replanner. Given the original goal, the available agents, "
    "the prior plan, and execution results so far, decide how to extend the plan "
    "to keep making progress toward the goal. Emit JSON ONLY in this exact shape:\n"
    '{{\n'
    '  "nodes": {{\n'
    '    "<new_id_1>": {{"agent": "<name>", "input": {{"goal": "<sub-goal>"}}}},\n'
    '    "<new_id_2>": {{"agent": "<name>", "input": {{"goal": "<sub-goal>"}}}},\n'
    '    "<new_id_3>": {{"agent": "<name>", "input": {{"goal": "<sub-goal>"}}}}\n'
    '  }},\n'
    '  "edges": [\n'
    '    ["<depends_on_or_existing_id>", "<new_id_1>"],\n'
    '    ["<depends_on_or_existing_id>", "<new_id_2>"],\n'
    '    ["<new_id_1>", "<new_id_3>"]\n'
    '  ],\n'
    '  "halt_reason": null,\n'
    '  "rationale": "<one short sentence explaining why>"\n'
    '}}\n'
    "Rules:\n"
    "- You MAY add multiple nodes per round — the example above shows three.\n"
    "- PREFER parallel fan-out: if the remaining work decomposes into independent "
    "sub-tasks, emit them all at once so they run concurrently, rather than "
    "chaining one node per round.\n"
    "- Keep each node's sub-goal focused and small enough to complete without "
    "truncation. If a prior node's output was truncated, split the remaining "
    "work into multiple smaller parallel nodes instead of one large follow-up.\n"
    "- Leave `halt_reason` null to keep running. Set it to one of "
    '`"done"`, `"abort"`, `"drift"` if the goal is achieved, impossible, or lost.\n'
    "- Only ADD nodes/edges. Do not modify or delete existing ones.\n"
    "- Use short, plain ids (e.g. 'r1', 'r2', 'r3') for extensions.\n"
    "- Do NOT re-emit a node whose goal already appears in the prior plan.\n"
    "- If you have nothing useful to add AND the goal is not yet achieved, return "
    '{{"nodes": {{}}, "edges": [], "halt_reason": null, "rationale": "no-op"}}.\n\n'
    "CONTEXT:\n"
    "goal={goal!r}\n"
    "agents={agents!r}\n"
    "prior_summary={prior_summary!r}\n"
    "completed={completed!r}\n"
    "failed={failed!r}\n"
    "pending={pending!r}\n"
)


_FORCE_EXTENSION_SUFFIX = (
    "\n\nADDITIONAL CONSTRAINT (overrides prior rules): "
    "You MUST add at least one new node in this iteration (multiple are "
    "encouraged — fan out if the follow-up decomposes into independent "
    "sub-tasks). Do NOT return `halt_reason`=\"done\" — always set it to "
    "null. Look at the completed results and propose follow-up node(s) that "
    "critique, validate, cross-check, or extend the existing work with "
    "concrete new sub-goals (e.g. an independent critique, a benchmark "
    "re-run, a missed dimension, or a production post-mortem verification). "
    "Each new node MUST depend on at least one already-completed node. "
    "Independent follow-ups SHOULD be emitted as parallel siblings, not "
    "a serial chain.\n"
)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()