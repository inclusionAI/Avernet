"""Bot interaction service contract.

Adapters depend on this ``api``-layer base class instead of the concrete
``core.service`` implementation, keeping the web delivery layer thin
(architecture Rule 7). The concrete ``DefaultBotInteractionService`` in
``core.service.bot_interaction`` subclasses this contract.
"""

from __future__ import annotations

from typing import Any

from ._models import (
    InteractionDispatch,
    InteractionRequestedResult,
    InteractionResolution,
    InteractionResolvedResult,
    InteractionResolveResult,
)


class BotInteractionService:
    """Transport-agnostic interaction state service contract."""

    def record_requested(
        self,
        *,
        session_key: str,
        interaction_id: str,
        envelope: dict[str, Any],
        allowed_decisions: tuple[str, ...],
        expires_at_ms: int | None,
    ) -> InteractionRequestedResult: ...

    def resolve(
        self,
        *,
        baas_interaction_id: str,
        resolution: InteractionResolution,
        request_envelope: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> InteractionResolveResult: ...

    def claim_for_dispatch(
        self, *, session_key: str, interaction_id: str
    ) -> InteractionDispatch | None: ...

    def should_poll(self, *, session_key: str, interaction_id: str) -> bool: ...

    def record_engine_exchange(
        self,
        *,
        session_key: str,
        interaction_id: str,
        engine_req: dict[str, Any],
        engine_res: dict[str, Any],
    ) -> bool: ...

    def mark_resolved(
        self,
        *,
        session_key: str,
        interaction_id: str,
        envelope: dict[str, Any],
    ) -> InteractionResolvedResult | None: ...

    def mark_expired(self, *, session_key: str, interaction_id: str) -> bool: ...

    def mark_failed(
        self, *, session_key: str, interaction_id: str, error: str
    ) -> bool: ...
