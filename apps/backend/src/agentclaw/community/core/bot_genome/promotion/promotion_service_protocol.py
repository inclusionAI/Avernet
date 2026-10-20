"""Service API of promotion (RSI-03), re-exported by ``api/bot_genome_promotion_service.py``."""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.bot_genome.models import BotRef, Promotion, PromotionPage, Provenance


@runtime_checkable
class GenomePromotionServiceProtocol(Protocol):
    @abstractmethod
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
        """Move ``ref`` (``active``) to ``revision`` by compare-and-swap, record it, hand it to delivery.

        ``revision`` is an id, ``r<seq>`` or a ref name; ``expected_revision``
        is where the caller believes ``active`` points now (``None``: not
        yet). Raises ``RevisionNotFound``, ``NotPromotable``, ``Drifted``
        (automated actors only), ``NotAppliable`` / ``PromotionUnsupported``
        from the seam's preflight (before any ref moves), ``RefConflict``.
        A failed delivery start is recorded as ``apply_failed`` on the
        promotion with the refs moved.
        """
        ...

    @abstractmethod
    def get_promotion(self, bot: BotRef, promotion_id: str) -> Promotion:
        """With the compiled document. Raises ``RevisionNotFound``."""
        ...

    @abstractmethod
    def list_promotions(self, bot: BotRef, *, page: int = 1, page_size: int = 20) -> PromotionPage:
        """Newest first, without documents."""
        ...
