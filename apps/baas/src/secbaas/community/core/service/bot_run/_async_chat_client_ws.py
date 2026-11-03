"""WebSocket-only transport implementation for ``AsyncChatClient``."""

from __future__ import annotations

from typing import Any, Literal

from ._async_chat_client_transport import (
    AsyncChatTransportBase,
    AsyncChatTransportOwner,
)
from ._bot_websocket_client import BotWebSocketClient


def make_ws_client(owner: AsyncChatTransportOwner) -> BotWebSocketClient:
    """Construct a WS endpoint; A2A may reuse it only for its explicit fallback."""
    return BotWebSocketClient(
        uri=owner.uri,
        client_id=owner.client_id,
        client_version=owner.client_version,
        headers=owner.headers,
    )


class WebSocketChatTransport(AsyncChatTransportBase):
    """Pure ``v3-ws`` transport.

    This path never constructs an A2A client, never reads A2A fallback state,
    and never adds session-key arguments to interaction calls.
    """

    @property
    def transport_name(self) -> str:
        return "v3-ws"

    async def create(self) -> tuple[Any, dict[str, Any]]:
        client = make_ws_client(self.owner)
        self.owner._bind_client_events(client)
        hello = await client.connect()
        return client, hello

    async def send_target(
        self,
        *,
        session_key: str,
        attachments: list[Any] | None,
        request_kind: Literal["send", "inject"] = "send",
    ) -> Any:
        return self.owner._client

    async def abort_target(self, *, session_key: str) -> Any:
        return self.owner._client

    async def interaction_target(
        self, *, session_key: str
    ) -> tuple[Any, dict[str, Any]]:
        return self.owner._client, {}

    def has_active_sessions(self) -> bool:
        client = self.owner._client
        return bool(getattr(client, "has_running_runs", False))

    def tracks_session(self, session_key: str) -> bool:
        client = self.owner._client
        inner_tracks = getattr(client, "tracks_session", None)
        return bool(inner_tracks and inner_tracks(session_key))

    async def close(self) -> None:
        return None
