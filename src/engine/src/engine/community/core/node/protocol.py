"""
NodeService Protocol — node-management interface.

Engines that maintain a registry of remote workers (OpenClaw via the
gateway's ``node.list`` RPC) implement this Protocol; engines that don't
return ``None`` for ``Engine.node`` and the manager raises
:class:`CapabilityNotSupportedError` on access.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.core.engine.context import AuthContext
from engine.community.core.node.models import Node, NodeListRequest


@runtime_checkable
class NodeService(Protocol):
    """Backend talks to node-aware engines through this Protocol."""

    async def list_nodes(
        self,
        request: NodeListRequest,
        auth: AuthContext | None = None,
    ) -> list[Node]:
        """List nodes matching the filter, with paging."""
        ...


__all__ = ["NodeService"]
