"""Static driver plugin: walk a DAG in dependency order, dispatching nodes to executors.

Concrete implementation of ``spi.Driver``. The business logic lives
here (next to other concrete plugins like ``StubLLMProvider`` and
``LLMBackedAgent``); ``core/_driver.py`` is now a backward-compat
shim that re-exports from this module.

The ``run()`` method is a template method with ``_before_run``,
``_after_wave``, ``_after_run`` hooks so dynamic drivers can subclass
without overriding the dispatch protocol. ``extension_history`` is
NOT written here — only dynamic drivers that invoke a replanner
append records.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any

from agentcompute.community.bootstrap import get_container

from .._logger import get_logger
from ..core._dag import DAGPlan
from ..core._node import DAGNode, NodeStatus
from ..spi._agent import AgentContext, NodeResult
from ..spi._driver import (
    Driver as _DriverSPI,
)
from ..spi._driver import (
    EventCallback,
    RunLog,
    RunResult,
)

__all__ = ["StaticDriver"]

ProgressCallback = Callable[[str, NodeStatus, float], None]

logger = get_logger("driver")


class StaticDriver(_DriverSPI):
    """Orchestrates DAG execution over short-lived agent executors.

    Ready nodes (all dependencies satisfied) within a wave run in parallel;
    waves advance in dependency order. ``max_workers`` bounds concurrency —
    set it to 1 for strictly sequential execution.

    Template method hooks (override in subclasses for dynamic behavior):
      - ``_before_run(plan)`` — after log reset, PENDING marking, validate.
      - ``_after_wave(plan, wave_idx, wave_succeeded, remaining) -> bool``
        — called after each wave dispatches. Return False to break the
        run loop (e.g., halt_reason). The ``remaining`` list can be
        mutated (e.g., to append newly added PENDING nodes after a
        replanner extension).
      - ``_after_run(plan, succeeded) -> RunResult`` — final assembly.
    """

    def __init__(
        self,
        on_progress: ProgressCallback | None = None,
        max_workers: int = 8,
        max_retries: int = 3,
    ) -> None:
        self._on_progress = on_progress
        self._max_workers = max_workers
        self._max_retries = max_retries
        self._lock = Lock()
        self._log = RunLog()
        self._on_event: EventCallback | None = None

    def run(
        self,
        plan: DAGPlan,
        *,
        on_progress: ProgressCallback | None = None,
        on_event: EventCallback | None = None,
        max_workers: int | None = None,
    ) -> RunResult:
        if on_progress is not None:
            self._on_progress = on_progress
        if max_workers is not None:
            self._max_workers = max_workers
        self._on_event = on_event
        self._before_run(plan)
        succeeded = self._run_loop(plan)
        return self._after_run(plan, succeeded)

    def _before_run(self, plan: DAGPlan) -> None:
        self._log = RunLog()
        for node in plan.nodes.values():
            node.status = NodeStatus.PENDING
            node.progress = 0.0
        plan.validate()
        logger.info(
            "driver starting run with %d node(s), %d edge(s)",
            len(plan.nodes),
            len(plan.edges),
        )

    def _run_loop(self, plan: DAGPlan) -> bool:
        succeeded = True
        wave_idx = 0
        remaining = [
            nid for nid in plan.topological_order()
            if plan.nodes[nid].status == NodeStatus.PENDING
        ]
        while remaining:
            wave, rest = self._take_ready(plan, remaining)
            if not wave:
                self._skip_remaining(plan, remaining)
                return False
            remaining = rest
            logger.info("driver dispatching wave: %s", wave)
            self._run_wave(plan, wave, wave_idx)
            wave_succeeded = not any(plan.nodes[n].status == NodeStatus.FAILED for n in wave)
            if not wave_succeeded:
                succeeded = False
            if not self._after_wave(plan, wave_idx, wave_succeeded, remaining):
                break
            wave_idx += 1
            remaining = [
                nid for nid in plan.topological_order()
                if plan.nodes[nid].status == NodeStatus.PENDING
            ]
        return succeeded

    def _after_wave(
        self,
        plan: DAGPlan,
        wave_idx: int,
        wave_succeeded: bool,
        remaining: list[str],
    ) -> bool:
        return True

    def _after_run(self, plan: DAGPlan, succeeded: bool) -> RunResult:
        final_output = self._final_output(plan)
        logger.info("driver run finished succeeded=%s", succeeded)
        return RunResult(
            plan=plan,
            log=self._log,
            succeeded=succeeded,
            final_output=final_output,
        )

    def _take_ready(self, plan: DAGPlan, node_ids: list[str]) -> tuple[list[str], list[str]]:
        ready = [
            nid
            for nid in node_ids
            if all(plan.nodes[d].status == NodeStatus.SUCCEEDED for d in plan.dependencies_of(nid))
            and all(plan.nodes[d].status != NodeStatus.FAILED for d in plan.dependencies_of(nid))
        ]
        rest = [nid for nid in node_ids if nid not in ready]
        return ready, rest

    def _run_wave(self, plan: DAGPlan, node_ids: list[str], wave_idx: int) -> None:
        logger.info("wave dispatching %d node(s): %s", len(node_ids), node_ids)
        for nid in node_ids:
            plan.nodes[nid].wave = wave_idx
        if self._max_workers == 1:
            for nid in node_ids:
                self._execute_node(plan, plan.nodes[nid])
            return
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = [
                pool.submit(contextvars.copy_context().run, self._execute_node, plan, plan.nodes[nid])
                for nid in node_ids
            ]
            for future in futures:
                future.result()

    def _skip_remaining(self, plan: DAGPlan, node_ids: list[str]) -> None:
        for nid in node_ids:
            node = plan.nodes[nid]
            node.status = NodeStatus.SKIPPED
            node.mark_finished()
            self._record(node)

    def _execute_node(self, plan: DAGPlan, node: DAGNode) -> None:
        with self._lock:
            node.status = NodeStatus.RUNNING
            node.mark_started()
        self._emit(node, 0.0)
        logger.info("node %s started (agent=%s)", node.id, node.agent)

        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                result = self._run_agent_once(plan, node)
                node.result = result.output
                with self._lock:
                    node.status = NodeStatus.SUCCEEDED
                    node.progress = 1.0
                    node.mark_finished()
                    self._log.results[node.id] = result.output
                logger.info("node %s succeeded", node.id)
                break
            except Exception as exc:
                last_exc = exc
                logger.warning("node %s attempt=%d/%d failed: %s", node.id, attempt, self._max_retries, exc)
        else:
            with self._lock:
                node.status = NodeStatus.FAILED
                node.mark_finished()
                node.error = str(last_exc)
                self._log.errors[node.id] = str(last_exc)
            logger.error("node %s failed after %d attempt(s): %s", node.id, self._max_retries, last_exc)

        self._record(node)
        self._emit(node, node.progress)

    def _run_agent_once(self, plan: DAGPlan, node: DAGNode) -> NodeResult:
        agent = None
        try:
            container = get_container()
            agent = container.plugins().agent(node.agent)
            agent.setup()
            with self._lock:
                upstream = {d: self._log.results.get(d) for d in plan.dependencies_of(node.id)}
            ctx = AgentContext(
                node_id=node.id,
                goal=str(node.input.get("goal", "")),
                upstream=upstream,
                on_progress=lambda p: self._emit(node, p),
            )
            return agent.execute(ctx)
        finally:
            if agent is not None:
                try:
                    agent.teardown()
                except Exception:
                    pass

    def _record(self, node: DAGNode) -> None:
        with self._lock:
            self._log.statuses[node.id] = node.status

    def _emit(self, node: DAGNode, progress: float) -> None:
        if self._on_progress is not None:
            self._on_progress(node.id, node.status, progress)

    def _final_output(self, plan: DAGPlan) -> Any:
        sinks = [n.id for n in plan.nodes.values() if not plan.dependents_of(n.id)]
        if len(sinks) == 1:
            return self._log.results.get(sinks[0])
        return {s: self._log.results.get(s) for s in sinks}