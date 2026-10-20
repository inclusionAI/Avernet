"""The delivery seam: ``RevisionApplier`` and this item's only implementation.

A promotion compiles a revision and hands the compiled document to an
applier. ``preflight`` runs before any ref moves and refuses what cannot be
delivered; ``start`` runs after the refs moved and the promotion row exists,
and answers with the applier's handle. RSI-04 (direct delivery) binds its
applier in place of :class:`PendingDeliveryApplier` in
``di/modules/bot_genome_module.py``, and nothing else on the promotion path
changes.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.bot_genome.errors import NotAppliable, PromotionUnsupported, Reason
from agentclaw.community.core.bot_genome.models import ApplyResult, BotRef, CompiledManifest, Provenance

__all__ = ["PendingDeliveryApplier", "RevisionApplier"]


@runtime_checkable
class RevisionApplier(Protocol):
    @property
    @abstractmethod
    def kind(self) -> str:
        """The applier's name on the promotion row (``apply_kind``)."""
        ...

    @abstractmethod
    def preflight(self, bot: BotRef, compiled: CompiledManifest) -> None:
        """Raise ``NotAppliable`` or ``PromotionUnsupported`` before any ref moves."""
        ...

    @abstractmethod
    async def start(self, bot: BotRef, compiled: CompiledManifest, *, actor: Provenance, reason: str) -> ApplyResult:
        """Deliver the compiled revision; the result's ``reference`` is what ``applied_revision`` joins on."""
        ...


class PendingDeliveryApplier(RevisionApplier):
    """Refuses every promotion before any ref moves: direct delivery is RSI-04."""

    kind = "pending"

    def preflight(self, bot: BotRef, compiled: CompiledManifest) -> None:
        if bot.bot_type == "service":
            raise PromotionUnsupported("promotion of a service bot goes through the publish flow, which RSI-04 / RSI-16 bind")
        raise NotAppliable(
            [Reason("not_appliable", "revision", "direct delivery of a compiled revision lands with RSI-04; nothing can deliver it yet")]
        )

    async def start(self, bot: BotRef, compiled: CompiledManifest, *, actor: Provenance, reason: str) -> ApplyResult:
        raise AssertionError("PendingDeliveryApplier.preflight refuses every promotion; start is never reached")
