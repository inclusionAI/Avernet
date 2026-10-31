"""``DirectApplier``: deliver a promotion's revision as an apply run, with no Manifest document in between.

The run carries only the state reference ``genome_state(promotion_id)``; the
apply worker compiles it back into the revision's desired state through the
genome compiler (``compile.py``) and applies that. The lock, the check and the
apply id are ``ApplyRunService.start_apply``'s, synchronous on the caller's
thread, so a busy bot is refused before the promotion moves anything.
"""

from __future__ import annotations

from agentclaw.community.core.apply_engine.run.service_protocol import ApplyInProgressError, ApplyRunServiceProtocol
from agentclaw.community.core.apply_engine.desired_state import DesiredState
from agentclaw.community.core.bot_genome.compile import genome_state
from agentclaw.community.core.bot_genome.errors import ApplyBusy
from agentclaw.community.core.bot_genome.models import BotRef
from agentclaw.community.core.bot_genome.promotion.applier import RevisionApplier
from agentclaw.community.core.repository.protocols.bot import BotRepository

__all__ = ["DirectApplier", "GENOME_PROMOTION_TRIGGER"]

#: The apply record's ``trigger`` for a promotion's delivery: what started it, for anyone reading the apply history.
GENOME_PROMOTION_TRIGGER = "genome_promotion"


class DirectApplier(RevisionApplier):
    def __init__(self, *, applies: ApplyRunServiceProtocol, bots: BotRepository) -> None:
        self._applies = applies
        self._bots = bots

    def preflight(self, bot: BotRef, state: DesiredState) -> None:
        """Takes every bot this item can address: a personal bot, or a service bot's draft (its own container).

        Promoting a service bot's verify or online stage goes through the publish pipeline
        (inclusionAI/Avernet#3212); this is where that will be refused until it exists.
        """

    def start(self, bot: BotRef, *, promotion_id: str, principal_id: str, audit_actor: str) -> str:
        record = self._bots.get_by_id_and_entity(bot.bot_id, bot.entity_id)
        if record is None:
            # The route resolved the bot a moment ago; a record gone now is a delivery that cannot start.
            raise LookupError(f"bot {bot.bot_id} has no record")
        try:
            accepted = self._applies.start_apply(
                entity_id=bot.entity_id,
                bot_id=bot.bot_id,
                bot=record,
                owner_id=bot.owner_id,
                actor_id=principal_id,
                audit_actor=audit_actor,
                state=genome_state(promotion_id),
                trigger=GENOME_PROMOTION_TRIGGER,
                phase=None,
            )
        except ApplyInProgressError as exc:
            raise ApplyBusy(f"another apply is running on bot {bot.bot_id}; wait for it to finish and retry") from exc
        return accepted.apply_id
