"""Nodes group — ``/openapi/v1/bots/{bot_id}/nodes``.

The current frontend and engine expose node inventory as a read-only list. This
public group mirrors only that capability; registration, removal and status
writes remain outside the contract until a real product flow and engine HTTP
surface exist for them.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.gating import (
    resolve_operable_bot,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.nodes.schemas import (
    Node,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
    StageQuery,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.errors import EngineUpstreamError
from agentclaw.community.di import Injected

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/nodes", tags=["nodes"])

NodeStatusQuery = Annotated[
    str | None,
    Query(max_length=128, description="Optional exact node-status filter."),
]
NodePlatformQuery = Annotated[
    str | None,
    Query(max_length=128, description="Optional exact node-platform filter."),
]
NodeLimitQuery = Annotated[
    int,
    Query(ge=1, le=100, description="Maximum nodes returned (max 100)."),
]
NodeOffsetQuery = Annotated[
    int,
    Query(ge=0, description="Zero-based number of matching nodes to skip."),
]


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _map_node(data: Any) -> Node:
    if not isinstance(data, dict):
        raise EngineUpstreamError("node list contains a non-object item")
    node_id = data.get("nodeId")
    status = data.get("status")
    if not isinstance(node_id, str) or not node_id:
        raise EngineUpstreamError("node list item carries no nodeId")
    if not isinstance(status, str) or not status:
        raise EngineUpstreamError("node list item carries no status")
    return Node(
        node_id=node_id,
        display_name=_optional_text(data.get("displayName")),
        platform=_optional_text(data.get("platform")),
        version=_optional_text(data.get("version")),
        capabilities=_string_list(data.get("capabilities")),
        commands=_string_list(data.get("commands")),
        remote_ip=_optional_text(data.get("remoteIp")),
        status=status,
    )


@router.get("", response_model=Envelope[list[Node]])
@envelope_errors
async def list_nodes(
    bot_id: BotIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    status: NodeStatusQuery = None,
    platform: NodePlatformQuery = None,
    limit: NodeLimitQuery = 20,
    offset: NodeOffsetQuery = 0,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[list[Node]]:
    """List nodes visible to the addressed bot runtime."""
    # COSEC: resolve the live owner/editor relationship before fetching the
    # device-wide node inventory; authorization failures stay masked as 404.
    facts = await resolve_operable_bot(
        relay,
        bot_id,
        caller_id=user_id,
        owner_id=owner_id,
        stage=stage.value,
        surface="nodes",
    )
    result = await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        method="GET",
        path="/api/nodes",
        params={
            key: value
            for key, value in {
                "status": status,
                "platform": platform,
                "limit": limit,
                "offset": offset,
            }.items()
            if value is not None
        },
    )
    if not isinstance(result.data, list):
        raise EngineUpstreamError("node list payload is not a list")
    return envelope([_map_node(item) for item in result.data], request)


__all__ = ["router"]
