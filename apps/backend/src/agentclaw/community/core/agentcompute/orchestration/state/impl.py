from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.core.agentcompute.domain.models import Run, AgentSpec


@dataclass
class RunState:
    run: Run
    round_index: int = 0
    turn_count: int = 0
    total_agents_spawned: int = 0
    last_spawn_objectives: list[str] = field(default_factory=list)
    routed_shard_count: int = 0
    recent_decision: Any = None
    active_persona: AgentSpec | None = None
