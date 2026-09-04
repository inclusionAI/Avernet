"""A bot's CLI tools — ``/openapi/v1/bots/{bot_id}/cli-tools``.

Three operations on their own router, mounted before the ``{bot_id}`` wildcard
group like every other bot-component group.

**Nothing about installing a tool is decided here.** The router resolves the
caller, hands the service a declaration, and shapes the result; the fetch, the
pin, the architecture check, the platform's copy of the bytes and the engine
call all live in ``core/bot_config_manifest/cli_tools``. That is the same
component a manifest's ``cli_tools`` category applies through, which is what
makes the two doors refuse the same declaration for the same reason.

**The bot may be someone else's.** These operations are collaborator-scoped —
MEMBER to read, ADMIN to write (``authorization.py``) — so the owner arrives as
``OwnerIdDep`` and the bot is resolved as *theirs*, while ``UserIdDep`` stays
the acting caller and is what ``installed_by`` records.

**No response carries a container path**, because the platform does not have
one: the engine chooses where a tool lands, inside the same call that installs
it.

**A tool is not a workspace file.** These are the only operations that manage
one, along with the bot's manifest; a CLI tool never appears in the resources
listing and cannot be written through it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CLI_TOOL_WRITE_RESPONSES,
    USER_SCOPED_403,
    BotIdPath,
    Deleted,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from fastapi import Path as PathParam
from agentclaw.community.adapters.http.openapi_v1.responses import (
    deleted as deleted_envelope,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_cli_tool_service import (
    BotCliToolRecord,
    BotCliToolServiceProtocol,
    CliToolDecl,
)
from agentclaw.community.di import Injected

from .schemas_cli_tools import CliTool, CliToolInstall, CliToolList


router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}",
    tags=["bots"],
    route_class=PublicAPIRoute,
)


def _view(record: BotCliToolRecord) -> CliTool:
    """One row, as the wire shape. Assembled field by field — there is no dict
    passthrough on this path, so no column reaches a caller unnamed."""
    return CliTool(
        name=record.name,
        version=record.version,
        digest=record.digest,
        subpath=record.subpath,
        md5=record.md5,
        size_bytes=record.size_bytes,
        installed_by=record.installed_by,
        gmt_modified=record.gmt_modified,
    )


@router.post(
    "/cli-tools",
    response_model=Envelope[CliTool],
    responses=CLI_TOOL_WRITE_RESPONSES,
    operation_id="install_bot_cli_tool",
)
@envelope_errors
async def install_bot_cli_tool(
    bot_id: BotIdPath,
    body: CliToolInstall,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    cli_tool_service: BotCliToolServiceProtocol = Injected(BotCliToolServiceProtocol),
) -> Envelope[CliTool]:
    """Install one command-line tool on a bot.

    The platform fetches the source, checks it against the mandatory `digest`,
    selects `subpath` out of an archive when one is declared, verifies that the
    result is an **x86-64 ELF executable**, keeps its own copy, and asks the
    bot's engine to install it. Nothing is recorded for a step that failed, so a
    200 means the bot has the tool.

    Answers **409** when the bot already has a tool by that name — a single
    install does not replace one you did not mention. Replacing a tool means
    deleting it first, or declaring the whole set in the bot's manifest, whose
    apply is a full override.

    A manifest apply that no longer declares this tool **will remove it**. The
    record's `installed_by` is what lets a report say so.
    """
    record = await cli_tool_service.install(
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
        decl=CliToolDecl(
            name=body.name,
            source_url=body.source,
            digest=body.digest,
            subpath=body.subpath,
            unpack=body.unpack,
            version=body.version,
            auth=body.auth,
        ),
    )
    return envelope(_view(record), request)


@router.get(
    "/cli-tools",
    response_model=Envelope[CliToolList],
    responses=USER_SCOPED_403,
    operation_id="list_bot_cli_tools",
)
@envelope_errors
async def list_bot_cli_tools(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    cli_tool_service: BotCliToolServiceProtocol = Injected(BotCliToolServiceProtocol),
) -> Envelope[CliToolList]:
    """Every CLI tool the platform records for a bot, in name order.

    This is the platform's own record, not a reading of the container. It is
    what a manifest apply computes removals from, so it is also the answer to
    "what would a full override replace".
    """
    records = cli_tool_service.list(
        bot_id=bot_id, owner_id=owner_id, actor_id=actor_id
    )
    return envelope(CliToolList(tools=[_view(r) for r in records]), request)


@router.delete(
    "/cli-tools/{name}",
    response_model=Envelope[Deleted],
    responses=USER_SCOPED_403,
    operation_id="delete_bot_cli_tool",
)
@envelope_errors
async def delete_bot_cli_tool(
    bot_id: BotIdPath,
    name: Annotated[
        str,
        PathParam(description="The command to remove, as it was installed."),
    ],
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    cli_tool_service: BotCliToolServiceProtocol = Injected(BotCliToolServiceProtocol),
) -> Envelope[Deleted]:
    """Remove one CLI tool from a bot.

    Removes it from the bot, drops the platform's record and deletes the copy of
    its bytes. Answers **404** when the bot has no tool by that name — unlike
    clearing a manifest, this is not idempotent, because "the tool is gone" and
    "you named the wrong tool" are worth telling apart.
    """
    await cli_tool_service.remove(
        bot_id=bot_id, owner_id=owner_id, actor_id=actor_id, name=name
    )
    return deleted_envelope(request)
