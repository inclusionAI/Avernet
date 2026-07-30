"""Connection endpoint — ``GET /openapi/v1/bots/{bot_id}/connection``.

The public replacement for the device-connection hand-off. Returns finished
socket URLs; the caller opens the socket itself. Chat is not relayed through
this API, so the engine's frame format never becomes a public contract.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.connection.schemas import (
    Connection,
    Socket,
)
from agentclaw.community.adapters.http.openapi_v1.principal import caller_owner_id
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.engine_connection_service import (
    EngineConnectionServiceProtocol,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.di import Injected

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}", tags=["connection"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]

#: Engine capability the terminal socket needs.
_WEB_SHELL_OPEN = "web_shell.open"


@router.get("/connection", response_model=Envelope[Connection])
@envelope_errors
async def get_connection(
    bot_id: str,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    connections: EngineConnectionServiceProtocol = Injected(
        EngineConnectionServiceProtocol
    ),
) -> Envelope[Connection]:
    """Get usable socket connections for a bot.

    The chat socket is always offered; a terminal socket appears only when the
    bot's engine supports one.
    """
    owner_id = caller_owner_id(principal)
    # One device call, for capabilities. The chat socket is derived from the
    # bot's active engine — a backend fact — so this is needed only to decide
    # whether a terminal socket exists. A failure here fails the endpoint with
    # the same not-ready answer as everything else, rather than silently
    # omitting a socket the bot does offer.
    caps = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="GET",
        path="/api/engine/capabilities",
    )
    raw = caps.data if isinstance(caps.data, dict) else {}
    declared = set(raw.get("supported") or []) | set(raw.get("limited") or [])

    result = connections.build(
        bot_id=bot_id,
        owner_id=owner_id,
        include_terminal=_WEB_SHELL_OPEN in declared,
    )
    return envelope(
        Connection(
            engine=result.engine,
            expires_at=result.expires_at,
            sockets=[
                Socket(kind=s.kind, url=s.url, headers=s.headers)
                for s in result.sockets
            ],
        ),
        request,
    )
