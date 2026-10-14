"""GuardService — behavioral guard checks over RunState (guard kinds are
persisted as ac_agent_swarm_guard_event rows by the swarm handler)."""
from __future__ import annotations

import time
import uuid

from agentclaw.community.core.agentswarm.domain.models import (
    EpochMillis, EventId, GuardEvent, GuardKind,
)
from agentclaw.community.core.agentswarm.orchestration.state import RunState


def _now_ms() -> int:
    return int(time.time() * 1000)


def _gen_id() -> str:
    return uuid.uuid4().hex


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
        """Depth guards lineage: ``depth`` is the spawn's lineage level
        (``ac_agent_swarm_agent.depth`` + ``parent_agent_id``). The swarm is
        flat — every orchestrator spawn is a root→child edge, so it always
        arrives at depth 1 and this guard stays dormant unless recursive
        spawning is ever added. In-flight concurrency is owned by max_width
        (``check_width``), and the total-spawn census by max_total_agents in
        the termination evaluator."""
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
            # The closed GuardKind set has no dedicated premature-finish
            # member: SERIAL_COLLAPSE is the accurate family (the
            # orchestrator ends the run alone with zero spawn activity),
            # and details["reason"] carries the exact event.
            return GuardEvent(
                id=EventId(_gen_id()),
                run_id=state.run.run_id,
                tenant=state.run.tenant,
                agent_id=None,
                kind=GuardKind.SERIAL_COLLAPSE,
                details={
                    "reason": "premature_finish",
                    "turns": state.turn_count,
                    "spawns": state.total_agents_spawned,
                    "routed_shards": state.routed_shard_count,
                },
                created_at=EpochMillis(_now_ms()),
            )
        return None