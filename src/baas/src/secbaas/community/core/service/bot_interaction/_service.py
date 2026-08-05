"""Core service for SSE + HTTP human interaction answers."""

from __future__ import annotations

import time
from dataclasses import dataclass

from secbaas.community.core.repository.bot_run_interaction import (
    BotRunInteractionPayload,
    BotRunInteractionPayloadPatch,
    BotRunInteractionRepository,
    JsonObject,
)


class InteractionServiceError(Exception):
    """Base interaction service error."""

    code = "INTERACTION_ERROR"


class InteractionBadRequestError(InteractionServiceError):
    code = "BAD_REQUEST"


class InteractionNotFoundError(InteractionServiceError):
    code = "NOT_FOUND"


class InteractionConflictError(InteractionServiceError):
    code = "CONFLICT"


@dataclass(frozen=True, slots=True)
class InteractionDispatch:
    """Validated command claimed by the websocket owner."""

    session_key: str
    interaction_id: str
    decision: str


@dataclass(frozen=True, slots=True)
class InteractionResolveResult:
    """Successful state transition returned to an uplink adapter."""

    interaction_id: str


class BotInteractionService:
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
        decision: str,
        request_envelope: JsonObject,
    ) -> InteractionResolveResult:
        record = self._repo.get(session_key=session_key, interaction_id=interaction_id)
        if record is None:
            raise InteractionNotFoundError("interaction not found")
        if record.state != "requested":
            raise InteractionConflictError(f"interaction state is {record.state}")
        if self._is_expired(record.payload.expires_at_ms):
            self.mark_expired(session_key=session_key, interaction_id=interaction_id)
            raise InteractionConflictError("interaction expired")
        if (
            record.payload.allowed_decisions
            and decision not in record.payload.allowed_decisions
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
                decision=decision,
                client_req=request_envelope,
            ),
        )
        if updated is None:
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
            decision=decision,
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
    def _is_expired(expires_at_ms: int | None) -> bool:
        return expires_at_ms is not None and int(time.time() * 1000) > expires_at_ms
