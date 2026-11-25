"""Apply intent for a Bot MCP override: a value replaces it, None clears it."""

from typing import TypeAlias, TypeVar

__all__ = ["McpConfigWriteIntent"]

_Config = TypeVar("_Config")
McpConfigWriteIntent: TypeAlias = _Config | None
