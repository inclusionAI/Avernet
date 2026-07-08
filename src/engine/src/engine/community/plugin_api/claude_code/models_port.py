"""ClaudeCodeModelsPort — native port for model + provider enumeration.

Models are pooled (client + relay), so port methods take
``token: str | None = None`` for per-token routing. Returns raw
list[dict] — the adapter builds the core DTOs.

Relay RPC mapping (teamclaw-aicoding-relay v3 protocol):

==========================  ================================================
Port method                 Relay RPC (method name on the wire)
==========================  ================================================
``models_list``             ``models.list``
``models_list_providers``   ``providers.list``
==========================  ================================================
"""
from __future__ import annotations

from typing import Protocol


class ClaudeCodeModelsPort(Protocol):
    """Native model + provider enumeration over the claude_code gateway (vendored Node relay)."""

    async def models_list(
        self,
        token: str | None = None,
    ) -> list[dict]:
        """Call ``models.list``; return raw model descriptor dicts.

        Args:
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def models_list_providers(
        self,
        token: str | None = None,
    ) -> list[dict]:
        """Call ``providers.list``; return raw provider descriptor dicts.

        Args:
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...


__all__ = ["ClaudeCodeModelsPort"]
