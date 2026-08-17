"""Bot-scoped MCP group — ``/openapi/v1/bots/{bot_id}/mcp``.

Which MCP servers a bot carries, and which of them its agent may call. The
shape mirrors the ``skills`` group — list, get, add, activate, deactivate,
remove — because it answers the same question about a different capability.

**This is the other half of a pair, and the halves are deliberately
independent.** The account-level group at ``/openapi/v1/bots/mcp`` holds the
*credential* for a server, keyed by ``(user_id, server_code)`` and shared across
all of that user's bots. This group holds whether a given bot uses the server.
Deleting a credential never deactivates a server here; removing a server here
never deletes the credential. A server can be added before its credential is
written, and the credential is picked up on the next sync.

Every route is user-scoped by ``?user_id=`` and resolves the bot with
``get_by_id_and_owner``, so a bot the caller does not own answers exactly as one
that does not exist. The group is mounted with ``_GRANT_CHECKED_SUBGROUPS``, so
an application acting under a grant reaches it on the same terms as the
equivalent ``skills`` operations.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request, Response, status

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.api.bot_mcp_state_service import BotMcpStateServiceProtocol
from agentclaw.community.di import Injected

from .schemas import (
    BotMcpServer,
    BotMcpServerAdd,
    BotMcpServerRemoved,
    BotMcpServerState,
)

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/mcp", tags=["bot-mcp"])

#: The path parameter naming the MCP server an operation addresses.
ServerCodePath = Annotated[
    str,
    Path(
        description="The MCP server's code, exactly as returned by the "
        "marketplace listing — an opaque, case-sensitive identifier, e.g. "
        "'mcp.example.weather'."
    ),
]


def _to_server(entry: dict) -> BotMcpServer:
    """Map one service entry to the public :class:`BotMcpServer`."""
    return BotMcpServer(
        server_code=entry["server_code"],
        name=entry["name"],
        description=entry.get("description"),
        active=bool(entry["active"]),
        is_default=bool(entry["is_default"]),
    )


def _to_state(result: dict) -> BotMcpServerState:
    return BotMcpServerState(
        server=_to_server(result["server"]), changed=bool(result["changed"])
    )


@router.get("", response_model=Envelope[Page[BotMcpServer]])
@envelope_errors
async def list_bot_mcp_servers(
    bot_id: BotIdPath,
    request: Request,
    page_params: PageParamsDep,
    owner_id: UserIdDep,
    state_service: BotMcpStateServiceProtocol = Injected(BotMcpStateServiceProtocol),
) -> Envelope[Page[BotMcpServer]]:
    """List the MCP servers on this bot, each with its active state.

    Includes the servers the engine supplies to every bot, marked as defaults,
    so the answer is "what can this bot call?" rather than only "what did I
    add?".
    """
    entries = state_service.list_bot_servers(bot_id=bot_id, owner_id=owner_id)
    start = max(page_params.page - 1, 0) * page_params.page_size
    window = entries[start : start + page_params.page_size]
    return page_envelope(
        len(entries), [_to_server(e) for e in window], request
    )


@router.get("/{server_code}", response_model=Envelope[BotMcpServer])
@envelope_errors
async def get_bot_mcp_server(
    bot_id: BotIdPath,
    server_code: ServerCodePath,
    request: Request,
    owner_id: UserIdDep,
    state_service: BotMcpStateServiceProtocol = Injected(BotMcpStateServiceProtocol),
) -> Envelope[BotMcpServer]:
    """Read one server's state on this bot.

    A server that is not on this bot answers not-found, identically to one that
    does not exist at all.
    """
    entry = state_service.get_bot_server(
        bot_id=bot_id, owner_id=owner_id, server_code=server_code
    )
    return envelope(_to_server(entry), request)


@router.post(
    "",
    response_model=Envelope[BotMcpServerState],
    status_code=status.HTTP_201_CREATED,
)
@envelope_errors
async def add_bot_mcp_server(
    bot_id: BotIdPath,
    body: BotMcpServerAdd,
    request: Request,
    response: Response,
    owner_id: UserIdDep,
    state_service: BotMcpStateServiceProtocol = Injected(BotMcpStateServiceProtocol),
) -> Envelope[BotMcpServerState]:
    """Add a marketplace MCP server to this bot, **deactivated**.

    Adding never changes what the agent can call — activating is a separate,
    explicit call. Idempotent: a server already on the bot answers 200 with
    changed false rather than erroring or duplicating it.

    A server that does not exist, or that the network-type rule hides, answers
    not-found — the same answer for both, so this is not a way to learn a server
    exists. No stored credential is required: one can be written before or after.
    """
    result = await state_service.add_bot_server(
        bot_id=bot_id, owner_id=owner_id, server_code=body.server_code
    )
    if not result["changed"]:
        # Nothing was created, so 201 would be a lie.
        response.status_code = status.HTTP_200_OK
        return envelope(_to_state(result), request)
    return envelope(_to_state(result), request, code=201000, message="Created")


@router.post("/{server_code}/activate", response_model=Envelope[BotMcpServerState])
@envelope_errors
async def activate_bot_mcp_server(
    bot_id: BotIdPath,
    server_code: ServerCodePath,
    request: Request,
    owner_id: UserIdDep,
    state_service: BotMcpStateServiceProtocol = Injected(BotMcpStateServiceProtocol),
) -> Envelope[BotMcpServerState]:
    """Let this bot's agent call the server.

    Idempotent — activating an already-active server succeeds with changed
    false. The bot's runtime is reconciled before the call returns, so a success
    means the agent can call it now, not eventually. A server that is not on the
    bot answers not-found rather than being silently added.
    """
    result = await state_service.set_bot_server_active(
        bot_id=bot_id, owner_id=owner_id, server_code=server_code, active=True
    )
    return envelope(_to_state(result), request)


@router.post("/{server_code}/deactivate", response_model=Envelope[BotMcpServerState])
@envelope_errors
async def deactivate_bot_mcp_server(
    bot_id: BotIdPath,
    server_code: ServerCodePath,
    request: Request,
    owner_id: UserIdDep,
    state_service: BotMcpStateServiceProtocol = Injected(BotMcpStateServiceProtocol),
) -> Envelope[BotMcpServerState]:
    """Stop this bot's agent calling the server, without removing it.

    Idempotent, reconciled before returning, and non-destructive: the server
    stays on the bot and can be reactivated without being re-added. Works on
    engine-supplied defaults too.
    """
    result = await state_service.set_bot_server_active(
        bot_id=bot_id, owner_id=owner_id, server_code=server_code, active=False
    )
    return envelope(_to_state(result), request)


@router.delete("/{server_code}", response_model=Envelope[BotMcpServerRemoved])
@envelope_errors
async def remove_bot_mcp_server(
    bot_id: BotIdPath,
    server_code: ServerCodePath,
    request: Request,
    owner_id: UserIdDep,
    state_service: BotMcpStateServiceProtocol = Injected(BotMcpStateServiceProtocol),
) -> Envelope[BotMcpServerRemoved]:
    """Take the server off this bot entirely.

    Removing a server the bot does not have succeeds reporting removed false.
    Removing an engine-supplied default answers 409 — it is synthesised
    for every bot rather than stored, so "not on this bot" is not a state it can
    hold; deactivate it instead.

    The caller's stored credential for the server is untouched: it is account
    state and outlives any one bot.
    """
    removed = await state_service.remove_bot_server(
        bot_id=bot_id, owner_id=owner_id, server_code=server_code
    )
    return envelope(
        BotMcpServerRemoved(server_code=server_code, removed=removed), request
    )
