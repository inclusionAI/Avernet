"""
Node data models.

Lifted from the legacy ``src/api/node.py`` ABC, then extended for plugin
dispatch. The frontend reads camelCase field names verbatim — keep the
shape stable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Node:
    """A node registered with the engine.

    `metadata` is engine-extensible; OpenClaw stuffs the raw upstream
    payload there so debugging tools can inspect the gateway's view.
    """

    nodeId: str
    displayName: str | None = None
    platform: str | None = None
    version: str | None = None
    capabilities: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    remoteIp: str | None = None
    status: str = "online"
    metadata: dict[str, Any] | None = None


@dataclass
class NodeListRequest:
    """Filters for :meth:`NodeService.list_nodes`."""

    status: str | None = None
    platform: str | None = None
    limit: int = 20
    offset: int = 0


__all__ = ["Node", "NodeListRequest"]
