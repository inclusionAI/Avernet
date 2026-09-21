"""Core port for reading the MCPs effectively consumed by one Bot."""

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EffectiveMCPStateReaderProtocol(Protocol):
    """Read the complete MCP supply used by runtime projection."""

    def effective_mcp_server_codes(
        self,
        *,
        bot_id: str,
        owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> frozenset[str]: ...


__all__ = ["EffectiveMCPStateReaderProtocol"]
