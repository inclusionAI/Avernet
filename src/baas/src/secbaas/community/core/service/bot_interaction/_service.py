"""Core service for SSE + HTTP human interaction answers."""

from __future__ import annotations

import time

from secbaas.community.api.bot_interaction import (
    BotInteractionService,
    InteractionBadRequestError,
    InteractionConflictError,
    InteractionDispatch,
    InteractionNotFoundError,
    InteractionResolution,
    InteractionResolveResult,
    InteractionServiceError,
)
from secbaas.community.core.repository.bot_run_interaction import (
    BotRunInteractionPayload,
    BotRunInteractionPayloadPatch,
    BotRunInteractionRepository,
    JsonObject,
)

# Re-export the contract types so existing ``core.service.bot_interaction``
# importers keep resolving them without touching the delivery layer.
__all__ = [
    "BotInteractionService",
    "DefaultBotInteractionService",
    "InteractionDispatch",
    "InteractionResolveResult",
    "InteractionServiceError",
]


class DefaultBotInteractionService(BotInteractionService):
    """Transport-agnostic interaction state service.

    Transport adapters validate protocol shapes and pass identity explicitly.
    The service never rediscovers ``session_key`` or ``interaction_id`` inside
    opaque protocol snapshots.
    """

    def __init__(self, repository: BotRunInteractionRepository) -> None:
        self._repo = repository

    def record_requested(
        self,
        *,
        session_key: str,
        interaction_id: str,
        envelope: JsonObject,
        allowed_decisions: tuple[str, ...],
        expires_at_ms: int | None,
    ) -> bool:
        result = self._repo.create_requested(
            session_key=session_key,
            interaction_id=interaction_id,
            payload=BotRunInteractionPayload(
                requested=envelope,
                allowed_decisions=allowed_decisions,
                expires_at_ms=expires_at_ms,
            ),
        )
        return result.created

    def resolve(
        self,
        *,
        session_key: str,
        interaction_id: str,
        resolution: InteractionResolution,
        request_envelope: JsonObject,
        idempotency_key: str | None = None,
    ) -> InteractionResolveResult:
        record = self._repo.get(session_key=session_key, interaction_id=interaction_id)
        if record is None:
            raise InteractionNotFoundError("interaction not found")
        resolution_payload = resolution.to_dict()
        if record.state != "requested":
            if self._is_idempotent_replay(
                record.payload,
                idempotency_key=idempotency_key,
                resolution_payload=resolution_payload,
            ):
                return InteractionResolveResult(interaction_id=interaction_id)
            raise InteractionConflictError(f"interaction state is {record.state}")
        if self._is_expired(record.payload.expires_at_ms):
            self.mark_expired(session_key=session_key, interaction_id=interaction_id)
            raise InteractionConflictError("interaction expired")
        allowed_decisions = record.payload.allowed_decisions
        if (
            allowed_decisions is not None
            and resolution.decision not in allowed_decisions
        ):
            raise InteractionBadRequestError(
                "decision is not allowed by interaction options"
            )

        updated = self._repo.transition(
            session_key=session_key,
            interaction_id=interaction_id,
            from_states=frozenset({"requested"}),
            to_state="queued",
            patch=BotRunInteractionPayloadPatch(
                decision=resolution.decision,
                client_req=request_envelope,
                resolution=resolution_payload,
                idempotency_key=idempotency_key,
            ),
        )
        if updated is None:
            latest = self._repo.get(
                session_key=session_key,
                interaction_id=interaction_id,
            )
            if latest is not None and self._is_idempotent_replay(
                latest.payload,
                idempotency_key=idempotency_key,
                resolution_payload=resolution_payload,
            ):
                return InteractionResolveResult(interaction_id=interaction_id)
            raise InteractionConflictError("interaction already resolved or queued")
        return InteractionResolveResult(interaction_id=interaction_id)

    def claim_for_dispatch(
        self, *, session_key: str, interaction_id: str
    ) -> InteractionDispatch | None:
        record = self._repo.get(session_key=session_key, interaction_id=interaction_id)
        if record is None or record.state != "queued":
            return None
        decision = record.payload.decision
        if decision is None:
            self.mark_failed(
                session_key=session_key,
                interaction_id=interaction_id,
                error="queued interaction has no decision",
            )
            return None
        try:
            resolution = (
                InteractionResolution.from_dict(record.payload.resolution)
                if record.payload.resolution is not None
                else InteractionResolution(decision=decision)
            )
        except ValueError:
            self.mark_failed(
                session_key=session_key,
                interaction_id=interaction_id,
                error="queued interaction has invalid resolution",
            )
            return None
        if resolution.decision != decision:
            self.mark_failed(
                session_key=session_key,
                interaction_id=interaction_id,
                error="queued interaction resolution decision does not match",
            )
            return None
        claimed = self._repo.transition(
            session_key=session_key,
            interaction_id=interaction_id,
            from_states=frozenset({"queued"}),
            to_state="dispatching",
            patch=BotRunInteractionPayloadPatch(),
        )
        if claimed is None:
            return None
        return InteractionDispatch(
            session_key=session_key,
            interaction_id=interaction_id,
            resolution=resolution,
        )

    def should_poll(self, *, session_key: str, interaction_id: str) -> bool:
        record = self._repo.get(session_key=session_key, interaction_id=interaction_id)
        return record is not None and record.state in {"requested", "queued"}

    def record_engine_exchange(
        self,
        *,
        session_key: str,
        interaction_id: str,
        engine_req: JsonObject,
        engine_res: JsonObject,
    ) -> bool:
        return (
            self._repo.merge_payload(
                session_key=session_key,
                interaction_id=interaction_id,
                allowed_states=frozenset({"dispatching"}),
                patch=BotRunInteractionPayloadPatch(
                    engine_req=engine_req,
                    engine_res=engine_res,
                ),
            )
            is not None
        )

    def mark_resolved(
        self,
        *,
        session_key: str,
        interaction_id: str,
        envelope: JsonObject,
    ) -> bool:
        return (
            self._repo.transition(
                session_key=session_key,
                interaction_id=interaction_id,
                from_states=frozenset({"requested", "queued", "dispatching"}),
                to_state="resolved",
                patch=BotRunInteractionPayloadPatch(resolved=envelope),
            )
            is not None
        )

    def mark_expired(self, *, session_key: str, interaction_id: str) -> bool:
        return (
            self._repo.transition(
                session_key=session_key,
                interaction_id=interaction_id,
                from_states=frozenset({"requested", "queued"}),
                to_state="expired",
                patch=BotRunInteractionPayloadPatch(
                    expire_reason="interaction deadline elapsed"
                ),
            )
            is not None
        )

    def mark_failed(self, *, session_key: str, interaction_id: str, error: str) -> bool:
        return (
            self._repo.transition(
                session_key=session_key,
                interaction_id=interaction_id,
                from_states=frozenset({"queued", "dispatching"}),
                to_state="failed",
                patch=BotRunInteractionPayloadPatch(dispatch_error=error),
            )
            is not None
        )

    @staticmethod
    def _is_idempotent_replay(
        payload: BotRunInteractionPayload,
        *,
        idempotency_key: str | None,
        resolution_payload: JsonObject,
    ) -> bool:
        if idempotency_key is None or payload.idempotency_key != idempotency_key:
            return False
        if payload.resolution != resolution_payload:
            raise InteractionConflictError(
                "idempotency key was reused with a different resolution"
            )
        return True

    @staticmethod
    def _is_expired(expires_at_ms: int | None) -> bool:
        return expires_at_ms is not None and int(time.time() * 1000) > expires_at_ms
