"""Connection endpoint — ``GET /openapi/v1/bots/connection/{bot_id}``.

The public replacement for the device-connection hand-off. Returns finished
socket URLs; the caller opens the socket itself. Chat is not relayed through
this API, so the engine's frame format never becomes a public contract.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.connection.schemas import (
    Connection,
    Socket,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
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
from agentclaw.community.api.engine_connection_service import (
    EngineConnectionServiceProtocol,
)
from agentclaw.community.di import Injected

router = APIRouter(prefix="/openapi/v1/bots/connection", tags=["connection"])


@router.get("/{bot_id}", response_model=Envelope[Connection])
@envelope_errors
async def get_connection(
    bot_id: str,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    connections: EngineConnectionServiceProtocol = Injected(
        EngineConnectionServiceProtocol
    ),
) -> Envelope[Connection]:
    """Get usable socket connections for a bot."""
    # No capability probe: the only socket offered is chat, derived from the
    # bot's active engine, which is a backend fact. The terminal socket that
    # once needed one was removed — the spec excludes an interactive shell from
    # v1 at any scope. That also removes a device call from this endpoint.
    # In a worker thread: ``build`` is synchronous and talks to the device
    # provider (device resolution, then ``get_device_connection``), which on the
    # BaaS path is a blocking ``httpx`` call with a 30-second timeout. Inline,
    # one slow provider lookup parks the event loop and stalls every unrelated
    # request on this worker. Offloading here rather than making ``build``
    # ``async`` keeps it callable from the sync paths and keeps its declared
    # signature — which ``test_service_api_conformance`` pins, coroutine status
    # included — the same on both sides.
    result = await asyncio.to_thread(
        connections.build,
        bot_id=bot_id,
        owner_id=owner_id,
        caller_id=user_id,
        stage=stage.value,
    )
    return envelope(
        Connection(
            engine=result.engine,
            expires_at=result.expires_at,
            sockets=[
                Socket(kind=s.kind, url=s.url)
                for s in result.sockets
            ],
        ),
        request,
    )
