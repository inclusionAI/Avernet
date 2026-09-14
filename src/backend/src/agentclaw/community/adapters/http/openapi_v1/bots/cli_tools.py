"""A bot's CLI tools — ``/openapi/v1/bots/{bot_id}/cli-tools``.

Three operations on their own router, mounted before the ``{bot_id}`` wildcard
group like every other bot-component group.

**Nothing about installing a tool is decided here.** The router resolves the
caller, hands the service the uploaded bytes and what to make of them, and
shapes the result; the pin, the unpacking, the architecture check, the
platform's copy of the bytes and the engine call all live in
``core/bot_config_manifest/cli_tools``. That is the same component a manifest's
``cli_tools`` category applies through, which is what makes the two doors
refuse the same executable for the same reason.

**The tool arrives in the request.** This endpoint takes a multipart upload —
one file part plus the form fields that describe it — exactly as the skill
package upload does. It takes no URL: the platform does not fetch on this road,
so it needs no credential to fetch under and no policy about which hosts it may
reach. A tool that lives at a *declared* source belongs in the bot's manifest,
where ``sources`` and their credentials are what a source is declared through.

**The body is bounded while it is read.** ``CliToolUploadRoute`` streams the
request against the category's own 200 MiB cap, so an over-sized upload is
refused as it arrives rather than after the platform has buffered it whole.

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

from typing import Annotated, Any, Awaitable, Callable

from fastapi import APIRouter, Form, HTTPException, Request
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.responses import Response

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
    mapped_error_response,
)
# Private on purpose, and imported on purpose: ``_bounded_request_stream`` is
# the one implementation of "stop reading at N bytes" this surface has, and the
# Space Skill upload is where it already lives. A second copy here would be a
# second place for the two to disagree about when a body is too large. Its
# signal never reaches a caller — it is translated below into this category's
# own refusal, with this category's own reason.
from agentclaw.community.adapters.http.openapi_v1.spaces.multipart_limits import (
    _SkillUploadTooLarge as _BodyTooLarge,
    _bounded_request_stream,
)
from agentclaw.community.api.bot_cli_tool_service import (
    BotCliToolRecord,
    BotCliToolServiceProtocol,
    CliToolDecl,
    CliToolTooLargeError,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FETCH_ENTRY_LIMITS,
    FetchCategory,
)
from agentclaw.community.di import Injected

from .schemas_cli_tools import CliTool, CliToolList, CliToolUpload


#: The category's own per-entry width (schema §5: 200 MiB), read from the one
#: table that states it rather than restated here. It caps the *fetch* for a
#: manifest-declared tool and the *upload* for this one, which is the point: a
#: binary the platform would refuse to fetch is not one it will accept by post.
_UPLOAD_LIMIT = FETCH_ENTRY_LIMITS[FetchCategory.CLI_TOOLS.value]

#: What the form fields and the multipart framing may add on top of the file.
#: Generous — the fields are a name, a digest and three short strings — because
#: its only job is to keep the framing from being counted against the caller.
_MULTIPART_OVERHEAD_BYTES = 64 * 1024


class CliToolUploadRoute(PublicAPIRoute):
    """The group's routes, with a receive-time bound on the one that uploads.

    The same shape ``spaces/multipart_limits.SpaceSkillPublicAPIRoute`` has, and
    for the same reason: FastAPI reads the form before a dependency runs, so a
    handler-side size check is a check on bytes the platform has already taken.
    Bounding the *stream* is what makes the refusal cost the caller's bandwidth
    rather than the platform's memory.
    """

    def __init__(
        self, path: str, endpoint: Callable[..., Any], **kwargs: Any
    ) -> None:
        methods = set(kwargs.get("methods") or ("GET",))
        self._bounded = "POST" in methods and path.endswith("/cli-tools")
        super().__init__(path, endpoint, **kwargs)

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()

        async def bounded_upload_handler(request: Request) -> Response:
            if not self._bounded or not request.headers.get(
                "content-type", ""
            ).startswith("multipart/form-data"):
                return await original(request)

            limit = _UPLOAD_LIMIT + _MULTIPART_OVERHEAD_BYTES
            declared_length = request.headers.get("content-length")
            if declared_length is not None:
                try:
                    if int(declared_length) > limit:
                        # The cheap refusal: a caller that announced the size
                        # is told before it sends a byte of the body.
                        return _too_large(request)
                except ValueError:
                    pass
            parser = MultiPartParser(
                request.headers,
                _bounded_request_stream(request, limit=limit),
                # One file and the fields that describe it. A second file part
                # is not a second tool — one entry is one command is one file.
                max_files=1,
                max_fields=8,
            )
            try:
                # FastAPI calls ``Request.form()`` before dependencies run, so
                # seeding its cache is the only way to keep the generated Form
                # model while the bounded parser is the one that read the body.
                request._form = await parser.parse()
            except _BodyTooLarge:
                return _too_large(request)
            except MultiPartException as exc:
                raise HTTPException(status_code=400, detail=exc.message) from exc
            return await original(request)

        return bounded_upload_handler


def _too_large(request: Request) -> Response:
    """This category's refusal, in the surface's envelope.

    The number in the message is the constant, not a copy of it: a cap that
    moved in the table and not in the sentence would tell the caller to try
    again under a limit that is no longer the one being enforced.
    """
    response = mapped_error_response(
        CliToolTooLargeError(
            "the uploaded tool exceeds the "
            f"{_UPLOAD_LIMIT // (1024 * 1024)} MiB limit for a CLI tool"
        ),
        request,
    )
    assert response is not None
    return response


router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}",
    tags=["bots"],
    route_class=CliToolUploadRoute,
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
    upload: Annotated[CliToolUpload, Form(media_type="multipart/form-data")],
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    cli_tool_service: BotCliToolServiceProtocol = Injected(BotCliToolServiceProtocol),
) -> Envelope[CliTool]:
    """Upload one command-line tool and install it on a bot.

    The body is `multipart/form-data`: the tool's bytes as the `file` part,
    plus the fields that say what to make of them. The platform checks the
    bytes against the mandatory `digest`, selects `subpath` out of an archive
    when `unpack` says there is one, verifies that the result is an **x86-64
    ELF executable**, keeps its own copy, and asks the bot's engine to install
    it. Nothing is recorded for a step that failed, so a 200 means the bot has
    the tool.

    Answers **422** when the uploaded bytes are not what `digest` says they
    are, when `subpath` names nothing in the archive, or when the file is not
    an x86-64 ELF executable; **409** when the bot already has a tool by that
    name — a single install does not replace one you did not mention. Replacing
    a tool means deleting it first, or declaring the whole set in the bot's
    manifest, whose apply is a full override. **413** when the upload is over
    200 MiB, refused while it is still arriving.

    A manifest apply that no longer declares this tool **will remove it**. The
    record's `installed_by` is what lets a report say so.
    """
    record = await cli_tool_service.install(
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
        decl=CliToolDecl(
            name=upload.name,
            digest=upload.digest,
            subpath=upload.subpath,
            unpack=upload.unpack,
            version=upload.version,
        ),
        data=await upload.file.read(),
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
