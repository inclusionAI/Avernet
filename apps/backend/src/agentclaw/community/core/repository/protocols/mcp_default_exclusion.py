"""Read contract for Bot-scoped platform-Default MCP exclusions."""

from abc import abstractmethod
from typing import Protocol, runtime_checkable


@runtime_checkable
class MCPDefaultExclusionReaderProtocol(Protocol):
    @abstractmethod
    def list_excluded_server_codes(
        self, *, bot_id: str, owner_id: str
    ) -> frozenset[str]: ...


__all__ = ["MCPDefaultExclusionReaderProtocol"]
