"""Internal chat capability contract; no implementation selection or owner access.

The facade calls operations; implementations publish events through explicitly
bound callbacks. Wire clients and conversation SessionState never cross this seam.
See ADR-0016 and docs/async-chat-transports.md for lifecycle semantics.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from secbaas.community.api.bot_interaction import InteractionResolution

from ._interaction_protocol import EngineInteractionResolveExchange

ChatTransportMode = Literal["off", "grey", "on"]
EventHandler = Callable[..., Any]
TokenMinter = Callable[[], Awaitable[str]]


def normalize_chat_transport_mode(value: str) -> ChatTransportMode:
    """Validate implementation selection at the assembly boundary."""
    if value not in {"off", "grey", "on"}:
        raise ValueError(f"invalid transport_mode: {value!r}")
    return cast(ChatTransportMode, value)


@dataclass(frozen=True)
class ChatTransportCallbacks:
    """Notifications, not access to a facade or mutable conversation state.

    Handlers are registered before connect and retained across main reconnect.
    Session failures are scoped to the supplied registered keys; the consumer
    decides how to terminate its own unfinished conversations.
    """

    events: Mapping[str, EventHandler]
    on_disconnect: EventHandler
    on_sessions_failed: Callable[[tuple[str, ...], str, str], None]


class ChatTransport(Protocol):
    """Internal consumer → transport contract (not an engine wire protocol)."""

    @property
    def uri(self) -> str: ...

    @property
    def is_connected(self) -> bool: ...

    @property
    def transport_name(self) -> str: ...

    def bind_events(self, callbacks: ChatTransportCallbacks) -> None: ...

    def refresh_outbound_auth(self, headers: dict[str, str] | None) -> None: ...

    async def connect(self) -> dict[str, Any]: ...

    async def reconnect(self) -> dict[str, Any]:
        """Replace the main connection only; preserve secondary running tasks."""
        ...

    async def close(self) -> None:
        """Dispose of all owned connections and transport run ownership."""
        ...

    async def send(
        self,
        *,
        session_key: str,
        message: str,
        auth_token: str | None,
        app_id: str | None,
        timeout_ms: int | None,
        chat_metadata: dict[str, str] | None,
        attachments: list[Any] | None,
    ) -> dict[str, Any]: ...

    async def inject(
        self,
        *,
        session_key: str,
        message: str,
        auth_token: str | None,
        chat_metadata: dict[str, str] | None,
        attachments: list[Any] | None,
    ) -> dict[str, Any]: ...

    async def abort(
        self, *, session_key: str, run_id: str | None
    ) -> dict[str, Any]: ...

    def can_resolve_interaction(self, session_key: str) -> bool:
        """Whether this session has an available interaction uplink, not just main."""
        ...

    async def resolve_interaction(
        self,
        *,
        session_key: str,
        interaction_id: str,
        resolution: InteractionResolution,
    ) -> EngineInteractionResolveExchange: ...

    def has_active_sessions(self) -> bool: ...

    def tracks_session(self, session_key: str) -> bool: ...


class ChatTransportFactory(Protocol):
    """Bootstrap-supplied assembly callable for dynamically pooled connections."""

    def __call__(
        self,
        *,
        uri: str,
        mode: ChatTransportMode,
        headers: dict[str, str] | None,
        ignore_case: bool,
        proxypass_token_minter: TokenMinter | None,
    ) -> ChatTransport: ...
