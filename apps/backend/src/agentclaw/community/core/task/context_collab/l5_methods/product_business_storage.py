"""Business-only read contract, NOT a replacement for RuntimeSnapshot.

Missing execution control/route facts are not fabricated as defaults. Until
those contracts are adopted this view must not be passed to RolloutEngine.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from .collet_rollout import ColletRollout


@dataclass(frozen=True)
class ProductBusinessHistory:
    collet: Collet
    owner: PrincipalRef
    current_rollout_id: int
    rollouts: tuple[ColletRollout, ...]
    # Explicit predecessor relationships, not guessed from increasing IDs.
    predecessors: dict[int, int]


@runtime_checkable
class ProductBusinessReader(Protocol):
    def get_collet(
        self, collet_id: int, context: ActorContext
    ) -> ProductBusinessHistory: ...
    def locate(
        self, identity: int, kind: str, context: ActorContext
    ) -> ProductBusinessHistory: ...
