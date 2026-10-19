"""Node-scoped HTTP routes for the single distributed Relay protocol."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.task.relay_guard import require_relay_attempt
from agentclaw.community.adapters.http.task.schemas_node_context import (
    NodeTaskContextDTO,
    node_context_to_dto,
)
from agentclaw.community.adapters.http.task.schemas_relay_dispatch import (
    TaskDispatchRequestDTO,
    TaskDispatchResultDTO,
)
from agentclaw.community.adapters.http.task.schemas_relay_search import (
    BotCandidateDTO,
    TaskSearchRequestDTO,
    TaskSearchResultDTO,
)
from agentclaw.community.adapters.http.task.schemas import BbsClaimDTO
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.di import Injected

router = APIRouter()


def _require_graph_node(graph, node_id: str):
    node = next((item for item in graph.tasks if item.node_id == node_id), None)
    if node is None:
        raise TaskStateError("relay node does not exist")
    return node


def _require_relay_graph(graph) -> None:
    if not graph.is_relay:
        raise TaskStateError("task is not in relay mode")


@router.get(
    "/{task_id}/nodes/{node_id}/context", response_model=Envelope[NodeTaskContextDTO]
)
@envelope_errors
async def get_relay_node_context(
    task_id: str,
    node_id: str,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[NodeTaskContextDTO]:
    graph = service.get_task_dashboard(task_id)
    _require_relay_graph(graph)
    node = _require_graph_node(graph, node_id)
    require_relay_attempt(node, request.headers.get("X-Relay-Token", ""))
    return envelope(
        node_context_to_dto(service.get_node_context(task_id, node_id)), request
    )


@router.post("/search", response_model=Envelope[TaskSearchResultDTO])
@envelope_errors
async def search_bots(
    body: TaskSearchRequestDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskSearchResultDTO]:
    if not body.task_id or not body.node_id or not body.relay_token:
        raise TaskStateError("relay search requires task_id, node_id and relay_token")
    graph = service.get_task_dashboard(body.task_id)
    _require_relay_graph(graph)
    node = _require_graph_node(graph, body.node_id)
    require_relay_attempt(node, body.relay_token)
    candidates = await service.search_bots(
        query=body.query,
        task_id=body.task_id,
        node_id=body.node_id,
    )
    return envelope(
        TaskSearchResultDTO(
            candidates=[
                BotCandidateDTO(
                    bot_id=item.bot_id,
                    name=item.name,
                    description=item.description,
                    score=item.score,
                )
                for item in candidates
            ]
        ),
        request,
    )


@router.post("/dispatch", response_model=Envelope[TaskDispatchResultDTO])
@envelope_errors
async def dispatch_relay_node(
    body: TaskDispatchRequestDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskDispatchResultDTO]:
    if not body.origin_node_id or not body.relay_token:
        raise TaskStateError("relay dispatch requires origin_node_id and relay_token")
    graph = service.get_task_dashboard(body.task_id)
    _require_relay_graph(graph)
    origin = _require_graph_node(graph, body.origin_node_id)
    target = _require_graph_node(graph, body.target_node_id)
    require_relay_attempt(origin, body.relay_token)
    if target.run_info.extend_props.get("relay_planned_by") != origin.node_id:
        raise TaskStateError("relay dispatch target was not planned by this origin")
    node = await service.dispatch_node(body.task_id, body.target_node_id)
    return envelope(
        TaskDispatchResultDTO(ok=True, node_id=node.node_id), request
    )


@router.post("/bbs/claim", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def bbs_claim(
    body: BbsClaimDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    if body.node_id is None:
        # Root-level BBS claiming is the centralized BBS API. It shares the
        # route prefix but is not a distributed Relay handoff and therefore
        # does not accept Relay tokens or invoke the Relay Skill protocol.
        result = service.claim_bbs_task(
            body.task_id,
            body.bot_id,
            None,
            claim_id=body.claim_id,
        )
        return envelope(
            {"root_node_id": result.node_id, "task_id": body.task_id}, request
        )
    graph = service.get_task_dashboard(body.task_id)
    _require_relay_graph(graph)
    result = service.claim_bbs_task(
        body.task_id,
        body.bot_id,
        body.node_id,
        claim_id=body.claim_id,
    )
    claimed = _require_graph_node(
        service.get_task_dashboard(body.task_id), result.node_id
    )
    return envelope(
        {
            "root_node_id": result.node_id,
            "task_id": body.task_id,
            "relay_token": claimed.run_info.extend_props.get("relay_token"),
        },
        request,
    )
