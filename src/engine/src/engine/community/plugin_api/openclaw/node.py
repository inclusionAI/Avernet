"""OpenClawNodePort — native port for node operations.

Node is token-agnostic (the gateway `node.list` RPC takes no auth/token routing),
so the port method takes no token. Returns raw gateway node payload dicts; the
`core/adapters/openclaw/node.py` adapter builds `Node` DTOs + applies the request
filter/paging.
"""
from __future__ import annotations

from typing import Any, Protocol


class OpenClawNodePort(Protocol):
    """Native node operations over the OpenClaw gateway."""

    async def node_list(self) -> list[dict[str, Any]]:
        """Raw node payload dicts from the `node.list` RPC. Returns `[]` on
        connection failure or a non-ok response (both logged)."""
        ...


__all__ = ["OpenClawNodePort"]
