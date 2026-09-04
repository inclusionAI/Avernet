"""Dynamic driver: extends the plan between waves using a Replanner.

Subclasses ``StaticDriver`` and overrides ``_after_wave`` to invoke the
``Replanner`` after each wave (or on failure, per ``replan_strategy``).
Extensions are merged transactionally: a failed merge leaves the plan
unchanged. Every applied extension is recorded on ``plan.extension_history``
so the final report can render the full plan evolution.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .._logger import get_logger
from ..core._dag import DAGPlan
from ..core._node import NodeStatus
from ..spi._driver import RunEvent, RunResult
from ..spi._replanner import HaltReason, ReplanError, Replanner
from ._static_driver import StaticDriver

__all__ = ["DynamicDriver"]

logger = get_logger("driver.dynamic")


class DynamicDriver(StaticDriver):
    def __init__(
        self,
        replanner: Replanner,
        replan_strategy: str = "after_wave",
        replan_max_calls: int = 10,
        replan_min_calls: int = 0,
        replan_cooldown_waves: int = 1,
        halt_on_replan_error: bool = False,
        persist_replans: bool = True,
        max_workers: int = 8,
        max_retries: int = 3,
    ) -> None:
        super().__init__(max_workers=max_workers, max_retries=max_retries)
        self._replanner = replanner
        self._replan_strategy = replan_strategy
        self._replan_max_calls = max(0, replan_max_calls)
        self._replan_min_calls = max(0, replan_min_calls)
        self._replan_cooldown_waves = max(1, replan_cooldown_waves)
        self._halt_on_replan_error = halt_on_replan_error
        self._persist_replans = persist_replans
        self._replan_calls_made = 0
        self._waves_since_last_replan = 0
        self._halt_decision: HaltReason | None = None

    def _after_wave(
        self,
        plan: DAGPlan,
        wave_idx: int,
        wave_succeeded: bool,
        remaining: list[str],
    ) -> bool:
        if self._halt_decision is not None:
            return False
        if self._has_ready_work(plan):
            return True
        self._waves_since_last_replan += 1
        if not self._should_replan(wave_succeeded):
            return True

        # replan_min_calls fires extra replan passes even on halt=DONE or no-op
        while True:
            if self._replan_calls_made >= self._replan_max_calls:
                logger.info(
                    "replan cycle done: max_calls=%d reached (calls_made=%d)",
                    self._replan_max_calls, self._replan_calls_made,
                )
                return True
            self._replan_calls_made += 1
            self._waves_since_last_replan = 0
            results, errors = self._collect_state(plan)
            logger.info(
                "replan call=%d/%d wave=%d ready_work=False",
                self._replan_calls_made, self._replan_max_calls, wave_idx,
            )
            self._emit_event(
                RunEvent(kind="replan_called", rationale=f"wave={wave_idx}")
            )
            try:
                extension = self._replanner.extend(
                    goal=plan.goal,
                    agents=plan.available_agents,
                    prior=plan,
                    results=results,
                    errors=errors,
                )
            except ReplanError as exc:
                self._emit_event(RunEvent(kind="replan_rejected", rationale=str(exc)))
                logger.warning("replanner failed: %s", exc)
                if self._halt_on_replan_error:
                    self._halt_decision = HaltReason.ABORT
                    return False
                return True

            if extension.halt_reason is not None:
                if (
                    extension.halt_reason == HaltReason.DONE
                    and self._replan_calls_made < self._replan_min_calls
                ):
                    rationale = (
                        f"halt=DONE ignored: replan_min_calls="
                        f"{self._replan_min_calls} not reached "
                        f"({self._replan_calls_made}/{self._replan_min_calls})"
                    )
                    self._emit_event(RunEvent(kind="replan_rejected", rationale=rationale))
                    logger.info("%s; calling again", rationale)
                    continue
                self._halt_decision = extension.halt_reason
                self._apply_extension(plan, extension, wave_idx)
                logger.info(
                    "replan halt=%s; new_nodes=%d edges=%d",
                    extension.halt_reason,
                    len(extension.extensions),
                    len(extension.new_edges),
                )
                return False

            if not extension.extensions and not extension.new_edges:
                self._emit_event(
                    RunEvent(kind="replan_rejected", rationale="no-op extension")
                )
                if self._replan_calls_made < self._replan_min_calls:
                    logger.info(
                        "replan no-op; replan_min_calls=%d not met (%d/%d); calling again",
                        self._replan_min_calls, self._replan_calls_made, self._replan_min_calls,
                    )
                    continue
                logger.info("replan no-op; halting (no more work to add)")
                return True

            merged = self._apply_extension(plan, extension, wave_idx)
            if merged:
                self._emit_event(
                    RunEvent(
                        kind="replan_applied",
                        extension_count=len(extension.extensions),
                        nodes_added=[n.id for n in extension.extensions],
                        rationale=extension.rationale,
                    )
                )
                logger.info(
                    "replan applied: new_nodes=%s edges=%d rationale=%r",
                    [n.id for n in extension.extensions],
                    len(extension.new_edges),
                    extension.rationale,
                )
            else:
                self._emit_event(
                    RunEvent(
                        kind="replan_rejected",
                        rationale="merge invalid; rolled back",
                    )
                )
                logger.warning("replan merge rolled back")
            return True

    @staticmethod
    def _has_ready_work(plan: DAGPlan) -> bool:
        """True if any PENDING node has all dependencies SUCCEEDED (or no deps).

        The replanner must NOT block already-ready work. We only invoke it
        when the plan is genuinely stuck (no pending node can make progress).
        This prevents a slow/timing-out replanner from gating a wave of
        ready nodes, and is the key invariant of the dynamic driver.
        """
        for node in plan.nodes.values():
            if node.status != NodeStatus.PENDING:
                continue
            deps = [src for src, dst in plan.edges if dst == node.id]
            if not deps:
                return True
            if all(plan.nodes[d].status == NodeStatus.SUCCEEDED for d in deps):
                return True
        return False

    def _after_run(self, plan: DAGPlan, succeeded: bool) -> RunResult:
        if self._halt_decision == HaltReason.ABORT:
            succeeded = False
        return super()._after_run(plan, succeeded)

    def _should_replan(self, wave_succeeded: bool) -> bool:
        if self._replan_strategy == "on_failure":
            return not wave_succeeded
        if self._replan_strategy == "after_wave":
            return self._waves_since_last_replan >= self._replan_cooldown_waves
        return False

    @staticmethod
    def _collect_state(plan: DAGPlan) -> tuple[dict[str, Any], dict[str, str]]:
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for nid, node in plan.nodes.items():
            if node.status == NodeStatus.SUCCEEDED:
                results[nid] = node.result
            elif node.status == NodeStatus.FAILED and node.error:
                errors[nid] = node.error
        return results, errors

    def _apply_extension(
        self,
        plan: DAGPlan,
        extension: Any,
        wave_idx: int,
    ) -> bool:
        snapshot_nodes = dict(plan.nodes)
        snapshot_edges = list(plan.edges)
        round_num = len(plan.extension_history) + 1
        try:
            for node in extension.extensions:
                node.plan_round = round_num
                plan.add_node(node)
            for src, dst in extension.new_edges:
                plan.add_edge(src, dst)
            plan.validate()
        except Exception as exc:
            plan.nodes = snapshot_nodes
            plan.edges = snapshot_edges
            logger.warning("extension merge rolled back: %s", exc)
            return False
        if self._persist_replans:
            record = {
                "wave_index": wave_idx,
                "applied_at": datetime.now(UTC).isoformat(),
                "rationale": extension.rationale,
                "halt_reason": (
                    extension.halt_reason.value if extension.halt_reason else None
                ),
                "nodes_added": [n.to_dict() for n in extension.extensions],
                "edges_added": [list(e) for e in extension.new_edges],
            }
            plan.extension_history.append(record)
        return True

    def _emit_event(self, event: RunEvent) -> None:
        if self._on_event is not None:
            self._on_event(event)