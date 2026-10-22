"""Persistence Plugin API, called by future hosting orchestration, not HTTP."""

from __future__ import annotations

from typing import Protocol

from engine.community.kernel.dws_hosting import (
    Binding,
    DirectMessage,
    IntakeResult,
    Turn,
    TurnOutcome,
)


class HostingStateStore(Protocol):
    """Atomic local state operations. Errors propagate; no external side effects.

    All times are trusted scheduler UTC epoch milliseconds. The caller owns
    authorization, time-of-day scheduling and history-reconciliation barriers.
    This port must not be exposed as a public management API without those gates.
    """

    def bind(self, binding: Binding) -> None:
        """Create disabled binding; exact retries succeed, identity changes fail."""
        ...

    def confirm_rule(self, binding_id: str, text: str) -> int:
        """Persist a confirmed rule and return its monotonically increasing version."""
        ...

    def set_enabled(self, binding_id: str, enabled: bool, *, now_ms: int) -> None:
        """Change enable epoch; discard queued work, never revive it on re-enable."""
        ...

    def set_takeover(
        self,
        binding_id: str,
        conversation_id: str,
        taken_over: bool,
        *,
        now_ms: int,
    ) -> None:
        """Change only this conversation's epoch and discard its queued work."""
        ...

    def ingest(
        self,
        binding_id: str,
        message: DirectMessage,
        *,
        now_ms: int,
    ) -> IntakeResult:
        """Deduplicate then persist; old, disabled and owner messages are not queued."""
        ...

    def claim_ready(self, binding_id: str, *, now_ms: int) -> Turn | None:
        """Claim one due batch with current rule; serialize each conversation."""
        ...

    def get_turn(self, binding_id: str, turn_id: str) -> Turn:
        """Read the original snapshot after restart; never reissue it automatically."""
        ...

    def is_current(self, binding_id: str, turn_id: str) -> bool:
        """Check enabled/takeover epochs, not latest rule version; NOT a send lease."""
        ...

    def finish_turn(self, binding_id: str, turn_id: str, outcome: TurnOutcome) -> None:
        """Unknown keeps the conversation blocked; confirmed completion releases it."""
        ...
