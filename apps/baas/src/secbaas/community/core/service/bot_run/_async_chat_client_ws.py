"""WS-only transport; no A2A probing, routing state or HTTP authentication."""

from __future__ import annotations

from typing import Any

from ._async_chat_transport_base import ChatTransportBase
from ._bot_websocket_client import BotWebSocketClient


def make_ws_client(
    *, uri: str, client_id: str, client_version: str, headers: dict[str, str]
) -> BotWebSocketClient:
    """Construct the stable WS wire client, also used by explicit fallback."""
    return BotWebSocketClient(
        uri=uri, client_id=client_id, client_version=client_version, headers=headers
    )


class WebSocketChatTransport(ChatTransportBase):
    @property
    def transport_name(self) -> str:
        return "v3-ws" if self._client is not None else "unknown"

    async def connect(self) -> dict[str, Any]:
        client = make_ws_client(
            uri=self.uri,
            client_id=self.client_id,
            client_version=self.client_version,
            headers=self.headers,
        )
        return await self._connect_client(client)
