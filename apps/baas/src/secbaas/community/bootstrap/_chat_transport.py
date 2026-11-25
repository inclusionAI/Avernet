"""Composition root for the existing off/grey/on chat transport selection."""

from ..core.service.bot_run._async_chat_client_transport import (
    ChatTransport,
    ChatTransportMode,
    TokenMinter,
    normalize_chat_transport_mode,
)


def build_chat_transport(
    *,
    uri: str,
    mode: ChatTransportMode,
    headers: dict[str, str] | None = None,
    ignore_case: bool = False,
    proxypass_token_minter: TokenMinter | None = None,
    client_id: str | None = None,
    client_version: str = "1.0.0",
) -> ChatTransport:
    """Keep imports lazy: selecting WS must not load the A2A implementation."""
    mode = normalize_chat_transport_mode(mode)
    if mode == "off":
        from ..core.service.bot_run._async_chat_client_ws import (
            WebSocketChatTransport,
        )

        return WebSocketChatTransport(
            uri, headers=headers, client_id=client_id, client_version=client_version
        )

    from ..core.service.bot_run._async_chat_client_a2a import (
        A2aChatTransport,
    )

    return A2aChatTransport(
        uri,
        mode,
        headers=headers,
        client_id=client_id,
        client_version=client_version,
        ignore_case=ignore_case,
        proxypass_token_minter=proxypass_token_minter,
    )
