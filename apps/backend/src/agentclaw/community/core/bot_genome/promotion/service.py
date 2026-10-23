"""``GenomePromotionService``: the 08-promotion.md §7.1 steps over the registry, the refs and the seam.

Order: resolve the target → refuse a candidate the gate has not accepted →
compile → drift (automated actors only) → ``applier.preflight`` → one
transaction holding the ``active``/``previous`` compare-and-swap, the
revision's ``promoted`` status and the promotion row with the compiled
document → ``applier.start`` → the row's delivery answer. Everything before
that transaction leaves no trace, and the transaction leaves all or nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from agentclaw.community.core.bot_genome.canonical import canonicalize, loads_strict
from agentclaw.community.core.bot_genome.errors import (
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
    GenomeRevisionRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

__all__ = ["GenomePromotionService", "PROMOTABLE"]

logger = get_logger()

#: Statuses a promotion accepts: an owner's draft, a gate-accepted candidate, or a revision promoted before.
PROMOTABLE = frozenset({RevisionStatus.DRAFT, RevisionStatus.ACCEPTED, RevisionStatus.PROMOTED})


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GenomePromotionService(GenomePromotionServiceProtocol):
    def __init__(
        self,
        *,
        registry: GenomeRegistryProtocol,
        revisions: GenomeRevisionRepositoryProtocol,
        refs: GenomeRefRepositoryProtocol,
        promotions: GenomePromotionRepositoryProtocol,
        applier: RevisionApplier,
        env: Callable[[], str] = get_current_env,
    ) -> None:
        self._registry = registry
        self._revisions = revisions
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
        compiled = self._registry.compile(bot, target.revision.id)
        active = self._refs.get(**self._axes(bot), name="active")
        if active is not None and actor.kind is not ProvenanceKind.USER:
            applied = self._registry.applied_revision(bot)
            if applied != active.revision_id:
                raise Drifted(active=active.revision_id, applied=applied)
        self._applier.preflight(bot, compiled)

        now = _now()
        row = self._promotions.start(
            GenomePromotionRow(
                **self._axes(bot),
                promotion_id=uuid.uuid4().hex,
                ref="active",
                revision_id=target.revision.id,
                from_revision_id=expected_revision,
                document_digest=compiled.document_digest,
                document_json=canonicalize(compiled.document).decode("utf-8"),
                going_back=status is RevisionStatus.PROMOTED and expected_revision != target.revision.id,
                actor_kind=actor.kind.value,
                actor_id=actor.audit,
                actor_run_id=actor.run_id,
                reason=reason,
                status="applying",
                apply_kind=self._applier.kind,
                created_at=now,
            ),
            expected_active=expected_revision,
            expected_statuses=tuple(s.value for s in PROMOTABLE),
            moved_at=now,
        )
        if row is None:
            # Either the ref moved under us or the revision's status did (verification can reject
            # an accepted candidate while we compile); the re-read says which.
            now_status = self._registry.get_revision(bot, target.revision.id).revision.status
            if now_status not in PROMOTABLE:
                raise NotPromotable(f"revision {target.revision.id} became {now_status.value} while promoting")
            current = self._refs.get(**self._axes(bot), name="active")
            raise RefConflict("active", expected=expected_revision, current=current.revision_id if current else None)
        try:
            result = await self._applier.start(bot, compiled, actor=actor, reason=reason)
        # The refs moved already: a delivery failure is the promotion's record, not the caller's crash.
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[genome.promotion] delivery did not start promotion=%s bot=%s: %s", row.promotion_id, bot.bot_id, exc
            )
            # The type only: delivery errors can quote headers or paths, and the row is never deleted.
            detail = (
                f"delivery did not start ({type(exc).__name__}); see the server log for promotion {row.promotion_id}"
            )
            finished = self._promotions.finish(
                **self._axes(bot),
                promotion_id=row.promotion_id,
                status="apply_failed",
                apply_reference=None,
                apply_detail=detail,
            )
        else:
            finished = self._promotions.finish(
                **self._axes(bot),
                promotion_id=row.promotion_id,
                status="applied",
                apply_reference=result.reference,
                apply_detail=result.detail[:1024],
            )
        return _promotion(finished or row, with_document=True)

    def get_promotion(self, bot: BotRef, promotion_id: str) -> Promotion:
        row = self._promotions.get(**self._axes(bot), promotion_id=promotion_id)
        if row is None:
            raise RevisionNotFound(promotion_id)
        return _promotion(row, with_document=True)

    def list_promotions(self, bot: BotRef, *, page: int = 1, page_size: int = 20) -> PromotionPage:
        total, rows = self._promotions.list(**self._axes(bot), page=page, page_size=page_size)
        return PromotionPage(total=total, items=tuple(_promotion(row, with_document=False) for row in rows))


def _promotion(row: GenomePromotionRow, *, with_document: bool) -> Promotion:
    return Promotion(
        id=row.promotion_id,
        ref=row.ref,
        revision=row.revision_id,
        from_revision=row.from_revision_id,
        document_digest=row.document_digest,
        going_back=bool(row.going_back),
        actor=Provenance(ProvenanceKind(row.actor_kind), row.actor_id, run_id=row.actor_run_id),
        reason=row.reason,
        status=row.status,
        apply_kind=row.apply_kind,
        apply_reference=row.apply_reference,
        apply_detail=row.apply_detail,
        created_at=row.created_at,
        document=loads_strict(row.document_json) if with_document else None,
    )
