"""Service API Protocol for a bot's MCP servers and their active state."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotMcpStateServiceProtocol(Protocol):
    """Service API for adding, listing, activating and removing a bot's MCPs.

    The bot-scoped half of the MCP surface. Its account-level counterpart —
    the credential — is ``MCPConfigServiceProtocol``; the two are deliberately
    separate axes, so nothing here reads or writes a stored api_key.
    """

    def list_bot_servers(self, *args: Any, **kwargs: Any) -> Any: ...

    def get_bot_server(self, *args: Any, **kwargs: Any) -> Any: ...

    async def add_bot_server(self, *args: Any, **kwargs: Any) -> Any: ...

    async def set_bot_server_active(self, *args: Any, **kwargs: Any) -> Any: ...

    async def remove_bot_server(self, *args: Any, **kwargs: Any) -> Any: ...
