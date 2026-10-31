"""The delivery seam: ``RevisionApplier``.

A promotion compiles a revision into the apply engine's desired state and
asks an applier two things. ``preflight`` runs before anything is written and
refuses a bot the applier cannot take; ``start`` runs once the promotion row
is filed and starts the delivery, answering the id of the apply run that
delivers it. How that run ends reaches the promotion through the apply
engine's completion publish (``promotion/settle.py``), not through the
applier. The binding is ``DirectApplier`` (``promotion/direct_applier.py``),
in ``di/modules/bot_genome_module.py``.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.apply_engine.desired_state import DesiredState
from agentclaw.community.core.bot_genome.models import BotRef

__all__ = ["RevisionApplier"]


@runtime_checkable
class RevisionApplier(Protocol):
    @abstractmethod
    def preflight(self, bot: BotRef, state: DesiredState) -> None:
        """Raise ``NotAppliable`` or ``PromotionUnsupported`` before anything is written."""
        ...

    @abstractmethod
    def start(self, bot: BotRef, *, promotion_id: str, principal_id: str, audit_actor: str) -> str:
        """Start delivering the promotion's revision; answer the apply run's id.

        ``principal_id`` is the user the apply's permission checks are made
        against, ``audit_actor`` the label its record shows. Raises
        ``ApplyBusy`` when another apply holds the bot's lock; any other
        raise is a delivery that did not start.
        """
        ...
