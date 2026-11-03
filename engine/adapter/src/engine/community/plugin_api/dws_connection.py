"""DWS connection port and durable reconciliation port (additive v1)."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from engine.community.kernel.dws_connection import (
    AuthorizationChallenge,
    AuthorizationStatus,
    ConnectionHealth,
    HistoryResult,
    HintWindow,
    ListenerNotice,
    RecoveryWindow,
)


class DwsConnection(Protocol):
    def exclusive(self) -> AbstractAsyncContextManager[None]:
        """Single owner of authorization/listener for this credential directory."""
        ...

    async def authorization_status(self) -> AuthorizationStatus: ...

    async def authorize(
        self,
        on_challenge: Callable[[AuthorizationChallenge], Awaitable[None]],
    ) -> AuthorizationStatus:
        """Device login, bounded and cancellable; keep credentials inside DWS."""
        ...

    def listen(self) -> AsyncIterator[ListenerNotice]:
        """Subscribe only to all-DM receive; clean up this consumer on exit."""
        ...

    async def health(self) -> ConnectionHealth: ...

    async def discover(self, start_ms: int, end_ms: int) -> tuple[str, ...]:
        """Return verified direct conversations only; partial pages must raise."""
        ...

    async def history(
        self, conversation_id: str, start_ms: int, end_ms: int
    ) -> HistoryResult:
        """Read bounded complete window, never mark read; partial pages must raise."""
        ...


class HostingRecoveryStore(Protocol):
    def recovery_window(
        self,
        binding_id: str,
        *,
        now_ms: int,
        overlap_ms: int,
        window_ms: int,
    ) -> RecoveryWindow | None:
        """Capture checkpoint; only startup/reconnect blocks all conversations."""
        ...

    def block_intake(self, binding_id: str) -> None: ...

    def record_hint(self, binding_id: str, notice: ListenerNotice) -> None:
        """Persist a DM event hint before fetching its canonical history message."""
        ...

    def remember_conversation(self, binding_id: str, conversation_id: str) -> None:
        """Only accept direct conversations verified by the connection provider."""
        ...

    def known_conversations(self, binding_id: str) -> tuple[str, ...]: ...

    def pending_hints(self, binding_id: str, *, now_ms: int) -> tuple[HintWindow, ...]:
        """Bounded canonical-history windows for hinted conversations only."""
        ...

    def complete_hints(self, window: HintWindow, observed_ids: tuple[str, ...]) -> None:
        """Epoch-fenced deletion of observed hints; never advances the global checkpoint."""
        ...

    def complete_recovery(self, window: RecoveryWindow) -> bool:
        """CAS checkpoint; unresolved hints fence their own conversation, not others."""
        ...

    def resolve_hints(
        self,
        window: RecoveryWindow,
        conversation_id: str,
        observed_ids: tuple[str, ...],
    ) -> None:
        """Resolve only IDs proven present in complete canonical history."""
        ...
