from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.core.agentswarm.domain.models import Run, AgentSpec


@dataclass
class RunState:
    run: Run
    round_index: int = 0
    turn_count: int = 0
    total_agents_spawned: int = 0
    last_spawn_objectives: list[str] = field(default_factory=list)
    routed_result_count: int = 0
    # Terminal agents that can never route a result (FAILED/KILLED — a
    # COMPLETED agent exits through routed_result_count). The width guard
    # subtracts them: in-flight is spawned − routed − this census, or a
    # genuinely-failed agent would occupy a width slot forever.
    terminal_without_result: int = 0
    # Every decide_turn observation, charged or not: the no-progress
    # window keys on this, while turn_count stays budgeted work only
    # (Wait/fully-deferred turns charge nothing — see orchestrator).
    decision_count: int = 0
    recent_decision: Any = None
    active_persona: AgentSpec | None = None
    # Refs of the LAST persisted wait decision — a resumed slice owes
    # durable work to those refs; any later decision resets it.
    pending_wait_refs: tuple[str, ...] = ()
