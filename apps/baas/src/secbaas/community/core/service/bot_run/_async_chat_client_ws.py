"""WebSocket-only transport implementation for ``AsyncChatClient``."""

from __future__ import annotations

from typing import Any, Literal

from secbaas.community.logger import get_logger

from ._async_chat_client_transport import AsyncChatTransportBase

logger = get_logger("core-bot-run")


class WebSocketChatTransport(AsyncChatTransportBase):
    """Pure ``v3-ws`` transport.

    This path never constructs an A2A client, never reads A2A fallback state,
    and never adds session-key arguments to interaction calls.
    """

    @property
    def transport_name(self) -> str:
        return "v3-ws"

    async def create(self) -> tuple[Any, dict[str, Any]]:
        client = self.owner._make_ws_client()
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
