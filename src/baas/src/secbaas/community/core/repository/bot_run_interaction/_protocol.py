"""Repository protocol for bot run interactions."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ._record import (
    BotRunInteractionCreateResult,
    BotRunInteractionPayload,
    BotRunInteractionPayloadPatch,
    BotRunInteractionRecord,
    InteractionState,
)


@runtime_checkable
class BotRunInteractionRepository(Protocol):
    def create_requested(
        self,
        *,
        session_key: str,
        interaction_id: str,
        payload: BotRunInteractionPayload,
    ) -> BotRunInteractionCreateResult: ...

    def get(
        self, *, session_key: str, interaction_id: str
    ) -> BotRunInteractionRecord | None: ...

    def transition(
        self,
        *,
        session_key: str,
        interaction_id: str,
        from_states: frozenset[InteractionState],
        to_state: InteractionState,
        patch: BotRunInteractionPayloadPatch,
    ) -> BotRunInteractionRecord | None: ...

    def merge_payload(
        self,
        *,
        session_key: str,
        interaction_id: str,
        allowed_states: frozenset[InteractionState],
        patch: BotRunInteractionPayloadPatch,
    ) -> BotRunInteractionRecord | None: ...
