"""Service API of promotion (RSI-03), re-exported by ``api/bot_genome_promotion_service.py``."""

from __future__ import annotations

from abc import abstractmethod
from typing import Literal, Protocol, runtime_checkable

from agentclaw.community.core.bot_genome.models import (
    BotRef,
    Promotion,
    PromotionPage,
    Provenance,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ManualPromotionAuthorization(BaseModel):
    """Trusted control-plane authorization, not a verification verdict.

    Backend rechecks exact provenance, active CAS and live drift. Only built-in
    human ADMIN ingress can supply this capability; strategies cannot obtain it.
    The immutable JSON is retained on the promotion for settlement and auditing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    basis: Literal["human"] = "human"
    candidate_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=64)
    revision_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    parent_revision_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strategy: str = Field(min_length=1, max_length=256)
    user_id: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=1000)
    authorized_at: str = Field(min_length=1, max_length=64)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("human authorization requires a reason")
        return value


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
        promotion without moving refs.
        """
        ...

    @abstractmethod
    async def promote_manual(self, bot: BotRef, authorization: ManualPromotionAuthorization) -> Promotion:
        """Apply exactly this human-authorized candidate through the platform seam.

        Idempotent by authorization.id, including response loss and concurrent
        requests. A previously claimed promotion is returned, never restarted.
        Explicit authorization does not change a verdict or mark a candidate
        accepted. Drift, stale parent and provenance mismatches always refuse.
        Pending after process loss is unknown, not permission to replay delivery.
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
