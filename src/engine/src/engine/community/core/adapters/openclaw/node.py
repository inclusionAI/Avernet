"""OpenClaw node ACL adapter.

Implements the core `NodeService` by delegating to an injected `OpenClawNodePort`
and translating the port's raw node dicts → `Node` DTOs, then applying the
request filter + paging. The `_to_node` builder lives here (core side) — the port
deals only in dicts.
"""
from __future__ import annotations

import logging
from typing import Any

from engine.community.core.engine.context import AuthContext
from engine.community.core.node.models import Node, NodeListRequest
from engine.community.core.node.protocol import NodeService
from engine.community.plugin_api.openclaw.node import OpenClawNodePort

log = logging.getLogger("openclaw-node-adapter")


def _to_node(data: dict[str, Any]) -> Node:
    """Convert an OpenClaw gateway node payload dict into a `Node`.

    Field mapping (relocated from `engines/openclaw/node.py:_to_node`):
      - `nodeId` → `nodeId`; `platform` → `platform`; `caps` → `capabilities`;
        `commands` → `commands`
      - `paired` + `connected` → derived `status`
        (`online` if connected, `paired` if only paired, else `offline`)
    """
    capabilities = list(data.get("caps") or [])
    commands = list(data.get("commands") or [])
    paired = bool(data.get("paired", False))
    connected = bool(data.get("connected", False))

    if connected:
        status = "online"
    elif paired:
        status = "paired"
    else:
        status = "offline"

    return Node(
        nodeId=data.get("nodeId", ""),
        displayName=data.get("displayName"),
        platform=data.get("platform"),
        version=data.get("version"),
        capabilities=capabilities,
        commands=commands,
        remoteIp=data.get("remoteIp"),
        status=status,
        metadata={"paired": paired, "connected": connected, "raw": data},
    )


class OpenClawNodeAdapter(NodeService):
    """`NodeService` over the OpenClaw native port."""

    def __init__(self, port: OpenClawNodePort) -> None:
        self._port = port

    async def list_nodes(
        self,
        request: NodeListRequest,
        auth: AuthContext | None = None,
    ) -> list[Node]:
        raw = await self._port.node_list()

        nodes: list[Node] = []
        for entry in raw:
            try:
                node = _to_node(entry)
            except Exception as e:  # noqa: BLE001
                log.warning(f"[list_nodes] convert failed: {e}")
                continue
            if request.status and node.status != request.status:
                continue
            if request.platform and node.platform != request.platform:
                continue
            nodes.append(node)

        start = request.offset
        end = start + request.limit
        return nodes[start:end]


__all__ = ["OpenClawNodeAdapter"]
