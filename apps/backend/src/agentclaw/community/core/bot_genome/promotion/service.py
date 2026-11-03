"""``GenomePromotionService``: the 08-promotion.md §7.1 steps over the registry, the seam and the settler.

Order: resolve the target → refuse a candidate the gate has not accepted →
compile → drift (automated actors only, observed by reading the bot) →
``applier.preflight`` — none of which writes anything — → file the promotion
``pending`` → ``applier.start`` (the apply run takes the bot's lock here,
synchronously) → ``applying`` with the apply id. The refs do not move here:
they move when the apply ends, in the transaction that settles the promotion
(``promotion/settle.py``), so ``active`` never names a revision the bot does
not hold.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from agentclaw.community.core.bot_genome.errors import (
    ApplyBusy,
    Drifted,
    NotPromotable,
    PromotionUnsupported,
    RefConflict,
    RevisionNotFound,
)
from agentclaw.community.core.bot_genome.genome_registry_protocol import GenomeRegistryProtocol
from agentclaw.community.core.bot_genome.models import (
    BotRef,
    Promotion,
    PromotionPage,
    PromotionStatus,
    Provenance,
    ProvenanceKind,
    RevisionStatus,
)
from agentclaw.community.core.bot_genome.promotion.applier import RevisionApplier
from agentclaw.community.core.bot_genome.promotion.promotion_service_protocol import (
    GenomePromotionServiceProtocol,
)
from agentclaw.community.core.bot_genome.repository.models import GenomePromotionRow
from agentclaw.community.core.repository.protocols.bot.genome import (
    GenomePromotionRepositoryProtocol,
    GenomeRefRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

__all__ = ["GenomePromotionService", "PROMOTABLE"]

logger = get_logger()

#: Statuses a promotion accepts: an owner's draft, a gate-accepted candidate, or a revision promoted before.
PROMOTABLE = frozenset({RevisionStatus.DRAFT, RevisionStatus.ACCEPTED, RevisionStatus.PROMOTED})

#: A ``pending`` row older than this with no apply belongs to a request that died between filing it and starting its
#: apply; the bot's next promotion settles it. Starting an apply takes milliseconds, so this is generous.
STALE_PENDING_SECONDS = 5 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GenomePromotionService(GenomePromotionServiceProtocol):
    def __init__(
        self,
        *,
        registry: GenomeRegistryProtocol,
        refs: GenomeRefRepositoryProtocol,
        promotions: GenomePromotionRepositoryProtocol,
        applier: RevisionApplier,
        env: Callable[[], str] = get_current_env,
    ) -> None:
        self._registry = registry
        self._refs = refs
        self._promotions = promotions
        self._applier = applier
        self._env = env

    def _axes(self, bot: BotRef) -> dict[str, str]:
        return {"env": self._env(), "entity_id": bot.entity_id, "bot_id": bot.bot_id}

    async def promote(
        self,
        bot: BotRef,
        *,
        revision: str,
        ref: str,
        expected_revision: str | None,
        actor: Provenance,
        reason: str,
    ) -> Promotion:
        if ref != "active":
            raise PromotionUnsupported(f"promotion moves 'active'; {ref!r} (canary rollout) is RSI-16")
        target = self._registry.get_revision(bot, revision)
        status = target.revision.status
        if status not in PROMOTABLE:
            raise NotPromotable(
                f"revision {target.revision.id} is {status.value}; "
                "only a draft, an accepted candidate or a promoted revision can be promoted"
            )
        state = self._registry.compile(bot, target.revision.id)
        if actor.kind is not ProvenanceKind.USER:
            # An automated actor may not promote over a bot that has moved by hand; an owner may.
            drift = await self._registry.drift(bot)
            if drift is not None:
                raise Drifted(active=drift.active, observed=drift.observed)
        self._applier.preflight(bot, state)
        current = self._refs.get(**self._axes(bot), name="active")
        if (current.revision_id if current is not None else None) != expected_revision:
            raise RefConflict("active", expected=expected_revision, current=current.revision_id if current else None)

        now = _now()
        row = self._promotions.insert(
            GenomePromotionRow(
                **self._axes(bot),
                promotion_id=uuid.uuid4().hex,
                ref="active",
                revision_id=target.revision.id,
                from_revision_id=expected_revision,
                going_back=status is RevisionStatus.PROMOTED and expected_revision != target.revision.id,
                actor_kind=actor.kind.value,
                actor_id=actor.audit,
                actor_run_id=actor.run_id,
                reason=reason,
                status=PromotionStatus.PENDING.value,
                created_at=now,
            )
        )
        # An automated actor's apply runs with the bot owner as its principal; the record names the actor.
        principal = actor.checked_as if actor.kind is ProvenanceKind.USER else bot.owner_id
        try:
            apply_id = self._applier.start(
                bot, promotion_id=row.promotion_id, principal_id=principal, audit_actor=actor.audit
            )
        except ApplyBusy:
            self._finish(bot, row.promotion_id, "another apply was running on this bot; nothing moved")
            raise
        except RefConflict:
            # Another promotion settled between the check above and this apply taking the bot's lock.
            self._finish(bot, row.promotion_id, "active moved before its apply started; nothing moved")
            raise
        except Exception as exc:  # noqa: BLE001 - recorded on the row; the refs never moved
            logger.warning(
                "[genome.promotion] delivery did not start promotion=%s bot=%s: %s", row.promotion_id, bot.bot_id, exc
            )
            # The type only: delivery errors can quote headers or paths, and the row is never deleted.
            return _promotion(
                self._finish(
                    bot,
                    row.promotion_id,
                    f"delivery did not start ({type(exc).__name__}); see the server log for promotion "
                    f"{row.promotion_id}",
                )
                or row
            )
        # This apply holds the bot's lock now, so no earlier promotion's apply is queued or running: an apply
        # whose request died after starting it held the lock until it ended, and its end settled that row.
        # An older `pending` row is one whose apply never started.
        self._promotions.fail_stale_pending(
            **self._axes(bot),
            older_than=now - timedelta(seconds=STALE_PENDING_SECONDS),
            apply_detail="the request that filed it ended before its apply started",
            settled_at=now,
        )
        applying = self._promotions.mark_applying(**self._axes(bot), promotion_id=row.promotion_id, apply_id=apply_id)
        # ``None`` only when the apply already ended and settled the row (a worker can be that quick): read it.
        return _promotion(applying or self._promotions.get(**self._axes(bot), promotion_id=row.promotion_id) or row)

    def _finish(self, bot: BotRef, promotion_id: str, detail: str) -> GenomePromotionRow | None:
        return self._promotions.finish(
            **self._axes(bot),
            promotion_id=promotion_id,
            status=PromotionStatus.APPLY_FAILED.value,
            apply_detail=detail,
        )

    def get_promotion(self, bot: BotRef, promotion_id: str) -> Promotion:
        row = self._promotions.get(**self._axes(bot), promotion_id=promotion_id)
        if row is None:
            raise RevisionNotFound(promotion_id)
        return _promotion(row)

    def list_promotions(self, bot: BotRef, *, page: int = 1, page_size: int = 20) -> PromotionPage:
        total, rows = self._promotions.list(**self._axes(bot), page=page, page_size=page_size)
        return PromotionPage(total=total, items=tuple(_promotion(row) for row in rows))


def _promotion(row: GenomePromotionRow) -> Promotion:
    return Promotion(
        id=row.promotion_id,
        ref=row.ref,
        revision=row.revision_id,
        from_revision=row.from_revision_id,
        going_back=bool(row.going_back),
        actor=Provenance(ProvenanceKind(row.actor_kind), row.actor_id, run_id=row.actor_run_id),
        reason=row.reason,
        status=PromotionStatus(row.status),
        apply_id=row.apply_id,
        apply_detail=row.apply_detail,
        created_at=row.created_at,
        settled_at=row.settled_at,
    )
