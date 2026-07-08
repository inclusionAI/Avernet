"""Node router — dispatches through ``EngineManager.node``.

Engine-specific behaviour (gateway ``node.list`` RPC for OpenClaw) lives
in the ``node`` plugin. The router only marshals HTTP query params into
:class:`NodeListRequest` and translates :class:`Node` back to the
camelCase dict shape the frontend expects.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from engine.community.api.caps import check_capability
from engine.community.api.response import ApiResponse
from engine.community.core.engine.capability import Capability
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.core.node.models import Node, NodeListRequest

log = logging.getLogger("api-nodes")

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


def _node_to_dict(node: Node) -> dict:
    return {
        "nodeId": node.nodeId,
        "displayName": node.displayName,
        "platform": node.platform,
        "version": node.version,
        "capabilities": list(node.capabilities or []),
        "commands": list(node.commands or []),
        "remoteIp": node.remoteIp,
        "status": node.status,
    }


def _node_plugin():
    from engine.community.manager import EngineManager
    return EngineManager.get_instance().node


@router.get("")
async def list_nodes(
    status: Optional[str] = None,
    platform: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> ApiResponse:
    warning = check_capability(Capability.NODE_LIST)
    try:
        plugin = _node_plugin()
        nodes = await plugin.list_nodes(
            NodeListRequest(
                status=status, platform=platform, limit=limit, offset=offset,
            )
        )
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.error(f"list_nodes error: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return ApiResponse(
        success=True,
        data=[_node_to_dict(n) for n in nodes],
        warning=warning,
    )
