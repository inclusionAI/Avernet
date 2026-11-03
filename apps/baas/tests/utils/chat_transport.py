"""Explicit chat assembly for existing orchestration and transport regressions."""

from secbaas.community.bootstrap._chat_transport import build_chat_transport
from secbaas.community.core.service.bot_run._async_chat_client import AsyncChatClient


def make_chat_client(
    uri,
    *,
    headers=None,
    client_id=None,
    client_version="1.0.0",
    transport_mode="off",
    proxypass_token_minter=None,
    **orchestration,
):
    transport = build_chat_transport(
        uri=uri,
        mode=transport_mode,
        headers=headers,
        client_id=client_id,
        client_version=client_version,
        ignore_case=orchestration.get("ignore_case", False),
        proxypass_token_minter=proxypass_token_minter,
    )
    return AsyncChatClient(transport=transport, **orchestration)
