"""Deliberate, scoped runs of the Installation flush — the backfill.

The flush also runs lazily in front of every capability read
(:class:`BotCapabilityStateReader`), which keeps Installation agreeing with
SkillSet configuration one Bot at a time. Configuration that reaches many Bots
at once has no per-Bot write to ride on: platform Default-Set content edited
through the admin tooling, an ``is_active`` flipped straight on the row, a
``center://`` membership resolving to a newly published version. Those Bots
converge only when something happens to read them.

This service runs the same flush deliberately — one Bot, or a page of them —
so an operator can converge that fan-out instead of waiting for reads to do
it. Like the flush, it is DB-side only: it never touches a device and never
triggers a runtime projection, so a Bot converged here still needs a
projection before its engine sees the change.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
    BackfillReport,
    BotBackfillOutcome,
    InstallationBackfillServiceProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class InstallationBackfillService(InstallationBackfillServiceProtocol):
    """Runs ``flush_installations`` over a chosen scope and reports what moved."""

    @inject
    def __init__(
        self,
        repository: CapabilityDesiredStateRepositoryProtocol,
        bot_repo: BotRepository,
    ) -> None:
        self._repository = repository
        self._bot_repo = bot_repo

    def backfill_bot(self, *, bot_id: str, owner_id: str) -> BotBackfillOutcome:
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        return BotBackfillOutcome(
            bot_id=bot_id,
            owner_id=owner_id,
            changed=self._flush(bot=bot, bot_id=bot_id, owner_id=owner_id),
        )

    def backfill_page(
        self,
        *,
        owner_id: str | None = None,
        engine_type: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> BackfillReport:
        total, bots = self._bot_repo.list_by_conditions(
            owner_id=owner_id,
            engine=engine_type,
            page=page,
            page_size=page_size,
        )
        outcomes = tuple(self._backfill_one(bot) for bot in bots)
        return BackfillReport(
            total=total,
            page=page,
            page_size=page_size,
            scanned=len(outcomes),
            changed=sum(1 for outcome in outcomes if outcome.changed),
            failed=sum(1 for outcome in outcomes if outcome.error is not None),
            outcomes=outcomes,
        )

    def _backfill_one(self, bot: Mapping[str, Any]) -> BotBackfillOutcome:
        """One Bot's flush, with its failure recorded rather than raised.

        A page is a sweep: one Bot whose Sets are malformed, or whose row lock
        is held, must not cost the rest of the page. The failure is reported
        in the outcome and counted, never swallowed into a success.
        """
        bot_id = str(bot["bot_id"])
        owner_id = str(bot["owner_id"])
        try:
            changed = self._flush(bot=bot, bot_id=bot_id, owner_id=owner_id)
        except Exception as exc:  # noqa: BLE001 — reported per Bot, see docstring
            logger.exception(
                "[installation_backfill] flush failed bot_id=%s owner_id=%s",
                bot_id,
                owner_id,
            )
            return BotBackfillOutcome(
                bot_id=bot_id, owner_id=owner_id, changed=False, error=str(exc)
            )
        return BotBackfillOutcome(
            bot_id=bot_id, owner_id=owner_id, changed=changed
        )

    def _flush(
        self, *, bot: Mapping[str, Any], bot_id: str, owner_id: str
    ) -> bool:
        """The same call the reader makes, scoped by the same engine helpers."""
        plan = self._repository.flush_installations(
            bot_id=bot_id,
            owner_id=owner_id,
            env=str(bot["env"]),
            engine_type=bot_engine_type(bot),
            default_engine_types=bot_default_engine_types(bot),
        )
        return plan.changed
