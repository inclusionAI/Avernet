"""The initial ``active`` baseline: the genome-owned answer to RSI cold start.

RSI never captures or promotes by itself — the RSI wiring tests pin that
boundary, and a missing baseline is answered with ``409 no_genome_baseline``.
The way out is this explicit genome operation, not a side effect of saving
RSI config. Recording the bot's current state and naming it the baseline
changes nothing on the running bot, so unlike a promotion this never
applies: the revision IS the current state by construction.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.bot_genome.errors import RefConflict
from agentclaw.community.core.bot_genome.genome_registry_protocol import GenomeRegistryProtocol
from agentclaw.community.core.bot_genome.models import (
    BotRef,
    Provenance,
    RefMover,
    RefUpdate,
    RevisionStatus,
)

__all__ = ["INITIAL_BASELINE_REASON", "Baseline", "ensure_active_baseline"]

INITIAL_BASELINE_REASON = "initial baseline: record the bot as it is; nothing is applied"


@dataclass(frozen=True)
class Baseline:
    """Where ``active`` points after the call, and whether this call put it there."""

    revision: str
    created: bool


async def ensure_active_baseline(
    registry: GenomeRegistryProtocol,
    bot: BotRef,
    *,
    actor: Provenance,
    reason: str = INITIAL_BASELINE_REASON,
) -> Baseline:
    """Name the bot's current state the ``active`` baseline when there is none yet.

    Idempotent: an existing ``active`` is returned untouched and concurrent
    first calls race on compare-and-swap — the loser observes the winner's
    baseline. The winning revision is labelled ``promoted`` afterwards, the
    same metadata a delivered promotion would leave behind.
    """

    for ref in registry.list_refs(bot):
        if ref.name == "active":
            return Baseline(ref.revision, created=False)
    recorded = await registry.capture(bot, created_by=actor)
    revision = recorded.revision.revision.id
    try:
        registry.move_ref(
            bot,
            RefUpdate("active", revision, None, reason=reason),
            actor=actor,
            mover=RefMover.PROMOTION,
        )
    except RefConflict:
        # A concurrent initializer won; its baseline is the baseline. If the
        # ref is somehow still absent the conflict is the honest error.
        for ref in registry.list_refs(bot):
            if ref.name == "active":
                return Baseline(ref.revision, created=False)
        raise
    registry.set_status(bot, revision, RevisionStatus.PROMOTED, actor=actor, reason=reason)
    return Baseline(revision, created=True)
