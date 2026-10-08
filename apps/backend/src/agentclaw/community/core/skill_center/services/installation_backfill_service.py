"""Deliberate runs of the Installation flush for one Bot — the backfill.

The flush also runs lazily in front of every capability read
(:class:`BotCapabilityStateReader`), which keeps Installation agreeing with
SkillSet configuration one Bot at a time. Configuration that reaches many Bots
at once has no per-Bot write to ride on: platform Default-Set content edited
through the admin tooling, an ``is_active`` flipped straight on the row, a
``center://`` membership resolving to a newly published version. Those Bots
converge only when something happens to read them.

This service runs the same flush deliberately, for one named Bot, so a
backfill can converge that fan-out instead of waiting for reads to do it.
Selecting the Bots and pacing the calls is the caller's job, not this
service's. Like the flush, it is DB-side only: it never touches a device and
never triggers a runtime projection, so a Bot converged here still needs a
projection before its engine sees the change.
"""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.skill_center.bot_engine_scope import (
    bot_default_engine_types,
    bot_engine_type,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.core.skill_center.installation_backfill_protocol import (
    InstallationBackfillServiceProtocol,
)


class InstallationBackfillService(InstallationBackfillServiceProtocol):
    """Runs ``flush_installations`` for one Bot, on demand."""

    @inject
    def __init__(
        self,
        repository: CapabilityDesiredStateRepositoryProtocol,
        bot_repo: BotRepository,
    ) -> None:
        self._repository = repository
        self._bot_repo = bot_repo

    def backfill_bot(self, *, bot_id: str, owner_id: str) -> None:
        """The same call the reader makes, scoped by the same engine helpers."""
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        self._repository.flush_installations(
            bot_id=bot_id,
            owner_id=owner_id,
            env=str(bot["env"]),
            engine_type=bot_engine_type(bot),
            default_engine_types=bot_default_engine_types(bot),
        )
