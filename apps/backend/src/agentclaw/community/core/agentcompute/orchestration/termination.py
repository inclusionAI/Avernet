from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.core.agentcompute.domain.models import (
    AgentSpecRaw, GuardEvent, Run, TerminationReason,
    EventId, EpochMillis, GuardKind,
)

import time


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class RunState:
    run: Run
    round_index: int = 0
    turn_count: int = 0
    total_agents_spawned: int = 0
    last_spawn_objectives: list[str] = field(default_factory=list)
    routed_shard_count: int = 0
    recent_decision: Any = None
    active_persona: AgentSpecRaw | None = None


class TerminationEvaluator:

    def evaluate(self, state: RunState) -> TerminationReason | None:
        phase = state.run
        if phase is None:
            return TerminationReason.ERROR
        if self._no_progress(state):
            return TerminationReason.NO_PROGRESS
        if state.turn_count >= self._max_turns:
            return TerminationReason.TURN_BUDGET
        if state.total_agents_spawned >= self._max_total_agents:
            return TerminationReason.MAX_AGENTS
        if self._wall_clock(state):
            return TerminationReason.WALL_CLOCK
        return None

    def __init__(self, max_turns: int = 50, max_total_agents: int = 100,
                 wall_clock_seconds: float = 3600.0,
                 no_progress_threshold: int = 3) -> None:
        self._max_turns = max_turns
        self._max_total_agents = max_total_agents
        self._wall_clock_seconds = wall_clock_seconds
        self._no_progress_threshold = no_progress_threshold

    def _no_progress(self, state: RunState) -> bool:
        if len(state.last_spawn_objectives) < self._no_progress_threshold:
            return False
        recent = state.last_spawn_objectives[-self._no_progress_threshold:]
        return all(o == recent[0] for o in recent) and state.routed_shard_count == 0

    def _wall_clock(self, state: RunState) -> bool:
        if state.run.started_at is None:
            return False
        elapsed_s = (_now_ms() - state.run.started_at) / 1000.0
        return elapsed_s >= self._wall_clock_seconds


class GuardService:

    def __init__(self, max_width: int = 8, max_depth: int = 5,
                 respawn_max: int = 3, no_progress_threshold: int = 3) -> None:
        self._max_width = max_width
        self._max_depth = max_depth
        self._respawn_max = respawn_max
        self._no_progress_threshold = no_progress_threshold

    def check_serial_collapse(self, state: RunState) -> GuardEvent | None:
        if state.total_agents_spawned == 0 and state.turn_count > self._no_progress_threshold:
            return GuardEvent(
                id=EventId(_gen_id()),
                run_id=state.run.run_id,
                tenant=state.run.tenant,
                agent_id=None,
                kind=GuardKind.SERIAL_COLLAPSE,
                details={"turns": state.turn_count, "spawns": state.total_agents_spawned},
                created_at=EpochMillis(_now_ms()),
            )
        return None

    def check_dedup(self, state: RunState, objective: str) -> GuardEvent | None:
        if objective.strip().lower() in [o.strip().lower() for o in state.last_spawn_objectives]:
            return GuardEvent(
                id=EventId(_gen_id()),
                run_id=state.run.run_id,
                tenant=state.run.tenant,
                agent_id=None,
                kind=GuardKind.DEDUPE_REJECT,
                details={"objective": objective},
                created_at=EpochMillis(_now_ms()),
            )
        return None

    def check_depth(self, state: RunState, depth: int) -> GuardEvent | None:
        if depth > self._max_depth:
            return GuardEvent(
                id=EventId(_gen_id()),
                run_id=state.run.run_id,
                tenant=state.run.tenant,
                agent_id=None,
                kind=GuardKind.DEPTH_EXCEEDED,
                details={"depth": depth, "max": self._max_depth},
                created_at=EpochMillis(_now_ms()),
            )
        return None

    def check_width(self, state: RunState, current_width: int) -> GuardEvent | None:
        if current_width >= self._max_width:
            return GuardEvent(
                id=EventId(_gen_id()),
                run_id=state.run.run_id,
                tenant=state.run.tenant,
                agent_id=None,
                kind=GuardKind.WIDTH_EXCEEDED,
                details={"width": current_width, "max": self._max_width},
                created_at=EpochMillis(_now_ms()),
            )
        return None

    def check_premature_finish(self, state: RunState) -> GuardEvent | None:
        if state.routed_shard_count == 0 and state.total_agents_spawned == 0 and state.turn_count > 0:
            return GuardEvent(
                id=EventId(_gen_id()),
                run_id=state.run.run_id,
                tenant=state.run.tenant,
                agent_id=None,
                kind=GuardKind.SERIAL_COLLAPSE,
                details={"reason": "premature_finish", "turns": state.turn_count},
                created_at=EpochMillis(_now_ms()),
            )
        return None


def _gen_id() -> str:
    import uuid
    return uuid.uuid4().hex