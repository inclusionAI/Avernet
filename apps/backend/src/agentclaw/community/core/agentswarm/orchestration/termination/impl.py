from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

from agentclaw.community.core.agentswarm.domain.models import TerminationReason
from agentclaw.community.core.agentswarm.orchestration.state import RunState

# Most runs end without evaluate() popping their window, so the dict is LRU-bounded.
_MAX_WINDOWS = 256


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class _NoProgressWindow:
    """Per-run no-progress tracking keyed by run_id.

    turn_count and routed_result_count are durable (rebuilt from rows on
    every slice), so a window survives rescheduled slices in this
    worker; a reclaim by a different worker just restarts its baseline.
    """

    last_turn: int = -1
    last_routed_result_count: int = -1
    last_spawn_count: int = -1
    flat_streak: int = 0


class TerminationEvaluator:

    def evaluate(self, state: RunState) -> TerminationReason | None:
        phase = state.run
        if phase is None:
            return TerminationReason.ERROR
        reason = self._reason(state)
        if reason is not None:
            # Terminal: the run can no longer be evaluated; drop its
            # window so sequential runs do not accumulate trackers.
            self._windows.pop(state.run.run_id, None)
        return reason

    def __init__(self, max_turns: int = 50, max_total_agents: int = 100,
                 wall_clock_seconds: float = 86400.0,
                 no_progress_threshold: int = 3) -> None:
        self._max_turns = max_turns
        self._max_total_agents = max_total_agents
        self._wall_clock_seconds = wall_clock_seconds
        self._no_progress_threshold = no_progress_threshold
        self._windows: OrderedDict[str, _NoProgressWindow] = OrderedDict()

    def _effective_turns(self, state: RunState) -> int:
        opts = getattr(state.run, "run_options", None)
        if opts is None:
            return self._max_turns
        return opts.resolved_max_turns(self._max_turns)

    def _effective_total_agents(self, state: RunState) -> int:
        opts = getattr(state.run, "run_options", None)
        if opts is None:
            return self._max_total_agents
        return opts.resolved_max_total_agents(self._max_total_agents)

    def _reason(self, state: RunState) -> TerminationReason | None:
        if self._no_progress(state):
            return TerminationReason.NO_PROGRESS
        if state.turn_count >= self._effective_turns(state):
            return TerminationReason.TURN_BUDGET
        if state.total_agents_spawned >= self._effective_total_agents(state):
            return TerminationReason.MAX_AGENTS
        if self._wall_clock(state):
            return TerminationReason.WALL_CLOCK
        return None

    def _no_progress(self, state: RunState) -> bool:
        run_id = state.run.run_id
        window = self._windows.get(run_id)
        if window is None:
            window = _NoProgressWindow()
            self._windows[run_id] = window
            if len(self._windows) > _MAX_WINDOWS:
                self._windows.popitem(last=False)
        else:
            self._windows.move_to_end(run_id)
        # Observation tick: any decided turn (decision_count advances on
        # every decide_turn call) or budgeted turn (turn_count) change.
        tick = (state.turn_count, state.decision_count)
        if window.last_turn != tick:
            # One observation per orchestrator turn (the handler evaluates
            # once after each decision). A turn is FLAT only when nothing
            # is in flight AND nothing new landed: routed results are real
            # progress, newly dispatched spawns are progress, and spawned-
            # but-unrouted agents are IN FLIGHT — a mid-provisioning cohort
            # (a BaaS persona takes ~90-120s) with spawned > routed is
            # activity by definition; a repeated WAIT on already-satisfied
            # refs during that window must not stack the streak.
            in_flight = state.total_agents_spawned - state.routed_result_count
            if state.routed_result_count > window.last_routed_result_count >= 0:
                window.flat_streak = 0
            elif in_flight > 0:
                window.flat_streak = 0
            elif window.last_routed_result_count >= 0:
                window.flat_streak += 1
            window.last_turn = tick
            window.last_routed_result_count = state.routed_result_count
            window.last_spawn_count = state.total_agents_spawned
        return window.flat_streak >= self._no_progress_threshold

    def _wall_clock(self, state: RunState) -> bool:
        if state.run.started_at is None:
            return False
        elapsed_s = (_now_ms() - state.run.started_at) / 1000.0
        return elapsed_s >= self._wall_clock_seconds
