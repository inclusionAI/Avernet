"""ClaudeCodeCommandsPort — native port for command enumeration.

Commands are pooled (client + relay), so port methods take
``token: str | None = None`` for per-token routing. Returns raw
list[dict] / dict | None — the adapter builds the core DTOs.

Relay RPC mapping (teamclaw-aicoding-relay v3 protocol):

==========================  ================================================
Port method                 Relay RPC (method name on the wire)
==========================  ================================================
``commands_list``           ``commands.list``
``commands_get``            ``commands.get``
==========================  ================================================
"""
from __future__ import annotations

from typing import Protocol


class ClaudeCodeCommandsPort(Protocol):
    """Native command enumeration over the claude_code gateway (vendored Node relay)."""

    async def commands_list(
        self,
        scope: str | None = None,
        token: str | None = None,
    ) -> list[dict]:
        """Call ``commands.list``; return raw command descriptor dicts.

        Args:
            scope: Optional scope filter (e.g. ``"builtin"``, ``"user"``).
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def commands_get(
        self,
        command_id: str,
        token: str | None = None,
    ) -> dict | None:
        """Call ``commands.get`` for a single command.

        Returns ``None`` when the command_id is not present.

        Args:
            command_id: The command identifier to look up.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...


__all__ = ["ClaudeCodeCommandsPort"]
