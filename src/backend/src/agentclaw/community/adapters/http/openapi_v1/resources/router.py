"""Resources group — ``/openapi/v1/bots/resources``.

A unified abstraction over files and links (a Yuque doc is a ``link`` resource);
the storage location is never exposed. All 9 handlers are wired to the slim
``core/resources/service.py`` ``ResourceService`` via ``ResourceServiceFactory``;
no legacy router private helper is imported (arch Rule 7 — thin adapter).

⚠️ STATUS: definition-only / NOT PUBLIC-READY. The handlers are wired to the
slim ``ResourceService`` and exercise the real service at the integration level,
but this surface is gated on the auth workstream before it is exposed to any
external tenant: ``require_principal`` is still a ``None`` stub, so the gateway's
signed-Principal seam is not in place yet. Do NOT expose to external callers
until that lands (see ``openapi_v1/dependencies.py`` and the cross-team tenant
isolation track in ``src/backend/docs/openapi-v1/README.zh-CN.md``).

Gates / follow-ups (block public-readiness, NOT a silent deployment):
- Owner/identity comes from ``UserIdDep`` — the request's own ``user_id``
  query parameter, refused unless it names the verified caller — mirroring the
  bots router. That dependency is the single replaceable point: when an App may
  act for a user, only ``principal.py`` changes.
- Cross-tenant isolation rides on the ac_bots guard (Phase 0); a deployed DDL
  for ``ac_resource.avernet_tenant`` MUST precede this code reaching prod (see
  Phase 0 plan) — code first / DDL later breaks bot reads with a missing column.
- device_fs resolution lives in the adapter (Rule 7 transport concern); a
  service owns the read/write via the opaque ``device_fs`` argument.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Body, HTTPException, Query, Request, Response

from agentclaw.community.api.resource_service import ResourceServiceFactoryProtocol
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    ErrorEnvelope,
    NameCheck,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted as deleted_envelope,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.core.bot_management.services.engine_resolver import (
    resolve_engine_for_bot,
)
from agentclaw.community.core.devices.services.device_filesystem import (
    FileTooLargeError as DeviceFileTooLargeError,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.resources.service import (
    DuplicateResourceError,
    FileTooLargeError,
    InvalidResourcePathError,
)
from agentclaw.community.core.services.resource_file_service import (
    ResourceFileService,
    is_readonly,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

from .schemas import Preview, Resource, ResourceCreate, ResourceType, ResourceUpdate

# ── openapi_v1 envelope + field-mapping helpers (Phase 1) ───────────
from agentclaw.community.core.resources.models import Resource as _LegacyResource
from agentclaw.community.core.resources.models import ResourceType as _LegacyType

logger = get_logger()


# legacy ResourceType → openapi ResourceType. R1a: legacy URL 归并进 openapi LINK.
# NODE/DATABASE/API 不在 openapi 契约 —— 读路径若出现,退回 LINK(读 list 不该报错);
# create 路径本期只接 LINK,不会经过这里。(legacy 无 FOLDER 枚举值,故无 mapping。)
_TYPE_MAP: dict[_LegacyType, ResourceType] = {
    _LegacyType.FILE: ResourceType.FILE,
    _LegacyType.LINK: ResourceType.LINK,
    _LegacyType.URL: ResourceType.LINK,
}

# openapi ResourceType → legacy ResourceType. R1a: openapi LINK 归并 legacy URL +
# LINK —— filter 侧本期只匹配 LINK(URL fan-out is a follow-up, see
# `_legacy_type_for` callers). openapi FOLDER has no legacy equivalent (no row
# in the legacy enum), so it's intentionally absent: list filters to empty and
# create raises 501 — they never reach the service with FOLDER.
_OPENAPI_TO_LEGACY_TYPE: dict[ResourceType, _LegacyType] = {
    ResourceType.FILE: _LegacyType.FILE,
    ResourceType.LINK: _LegacyType.LINK,
}


def _legacy_type_for(openapi_type: ResourceType | None) -> _LegacyType | None:
    """Map an openapi ResourceType to the legacy ResourceType the slim service expects.

    Returns None when:
    - ``openapi_type`` is None (no filter — caller passes None through), or
    - ``openapi_type`` is FOLDER: there is no legacy equivalent (FOLDER is an
      openapi-only type with no backing enum value). Callers that must NOT
      fall through to "no filter" (list_resources) distinguish these two None
      cases via the ``type`` argument directly — see list_resources handler.
    """
    if openapi_type is None:
        return None
    return _OPENAPI_TO_LEGACY_TYPE.get(openapi_type)


def _to_openapi_resource(legacy: _LegacyResource) -> Resource:
    """Map a legacy domain Resource → public openapi Resource schema.

    Flattens type-specific attributes (url/size) to top-level fields and never
    exposes the storage location. Per arch Rule 7 (mapping = protocol concern).
    """
    return Resource(
        resource_id=str(legacy.id) if legacy.id is not None else "",
        name=legacy.name or "",
        type=_TYPE_MAP.get(legacy.resource_type, ResourceType.LINK),
        source=legacy.source,
        url=legacy.url,
        size=legacy.size if legacy.resource_type == _LegacyType.FILE else None,
        # ``None``, not ``""``: the field is nullable now, and an empty string is
        # a sentinel pretending to be a timestamp. Absent means absent.
        gmt_create=legacy.gmt_created.isoformat() if legacy.gmt_created else None,
        gmt_modified=legacy.gmt_modified.isoformat() if legacy.gmt_modified else None,
    )


#: Preview cap, legacy parity with the former service-level default.
_PREVIEW_MAX_BYTES = 1_048_576

# Per-route failures, declared where they happen. ``contracts.ERROR_RESPONSES``
# is applied surface-wide and deliberately holds only the statuses every route
# can answer; a status one group produces belongs here, the way ``skills``
# declares its own 413 and ``USER_SCOPED_403`` declares its 403. Without these
# the published schema omits responses these routes really do return, so a
# generated client cannot model them.
_TOO_LARGE_RESPONSE: dict[int | str, dict[str, object]] = {
    413: {
        "model": ErrorEnvelope,
        "description": "File exceeds the size the provider will serve, or the "
        "1 MB preview cap.",
    },
}
# The group is already user-scoped, so it carries ``USER_SCOPED_403`` from
# assembly and a route-level 403 *replaces* that entry rather than adding to it.
# Delete can answer 403 for either reason, so the description has to name both —
# stating only the new one would silently drop the meaning every other route
# here documents.
_READ_ONLY_RESPONSE: dict[int | str, dict[str, object]] = {
    403: {
        "model": ErrorEnvelope,
        "description": "The path is read-only — a dotfile, or a workspace-root "
        "identity file — or the user_id names a user the authenticated caller "
        "may not act for.",
    },
}


def _safe_path(path: str) -> str:
    """Normalize a caller-supplied workspace-relative path, or reject it.

    Rejects any ``..`` segment outright instead of filtering it out. The console's
    ``ResourceFileService.upload_file`` drops such segments silently
    (``core/services/resource_file_service.py:409``) because it has to accept
    whatever a browser sends for a whole-folder drag-upload; an explicit API has
    no such caller, and quietly rewriting an address to a *different* valid one is
    worse than refusing it — the caller is told nothing, and the file lands
    somewhere they did not name.

    This is the only barrier: neither ``build_workspace_mapper`` (which composes
    with ``Path.__truediv__``, leaving ``..`` intact) nor the engine's
    ``_convert_path`` normalizes or asserts containment. Engine-side bounding is
    tracked in #1002.

    Leading slashes and empty / ``.`` segments are normalized away rather than
    rejected: they are noise, not an attempt to leave the workspace.
    """
    segments = [s for s in path.split("/") if s and s != "."]
    if any(s == ".." for s in segments):
        raise InvalidResourcePathError(f"path escapes the workspace: {path!r}")
    return "/".join(segments)


def _reject_read_only(safe: str) -> None:
    """Refuse a path the read-only policy protects, with the console's 403.

    Applied to **creation** as well as deletion, so that the surface cannot be
    talked into making something it then refuses to manage: a listing hides
    dotfiles and the root identity files, and delete refuses them, so an upload
    or mkdir that accepted one would leave an entry this API can neither show
    nor remove. Uploading a workspace-root identity file would also overwrite
    the bot's own configuration through a resource endpoint, which is not what
    this surface is for.

    Every ancestor is checked, not just the whole path. ``is_readonly`` looks at
    the final segment only, so ``.private/file.md`` passes it — the leaf is an
    ordinary name — while creating it brings a hidden ``.private`` directory into
    existence along the way. Removing the visible descendant afterwards would
    then leave a directory this API created and can neither list nor delete.
    """
    segments = safe.split("/")
    for depth in range(len(segments)):
        ancestor = "/".join(segments[: depth + 1])
        if is_readonly(ancestor):
            raise HTTPException(
                status_code=403, detail="Cannot write to a read-only path"
            )


def _file_coords(
    bot_id: str, owner_id: str, bot_repo: BotRepository
) -> tuple[str, str, str]:
    """``(entity_type, entity_id, engine_type)`` for the file endpoints.

    ``ResourceFileService`` addresses a bot's workspace by these three
    coordinates, and ``DeviceContext`` carries none of them — it holds
    provider / conn_info / binding only. Mirrors the console router's
    ``_resolve_params`` (``adapters/http/resources/file_router.py:71``): the
    entity is the bot owner, and ``engine_type`` defaults to the bot's
    ``active_engine``. ``entity_type`` is ``"staff"``, matching
    ``ResourceFileService``'s own default.
    """
    engine_type = resolve_engine_for_bot(
        bot_id=bot_id, owner_id=owner_id, override=None, bot_repo=bot_repo
    )
    return ("staff", owner_id, engine_type)


router = APIRouter(prefix="/openapi/v1/bots/resources", tags=["resources"])


@router.get("", response_model=Envelope[Page[Resource]])
@envelope_errors
async def list_resources(
    page: PageParamsDep,
    owner_id: UserIdDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    path: str = Query(
        "", description="Directory to list, relative to the workspace root."
    ),
    type: ResourceType | None = None,
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[Page[Resource]]:
    """List a directory of the bot's workspace, plus the bot's link resources.

    `path` selects the directory; omit it for the workspace root. The listing is
    not recursive — read a subdirectory by listing it in turn.

    Files and folders are read from the workspace itself, so a file the bot
    produced on its own is listed alongside one you uploaded, and a file that is
    no longer there is not listed at all. Entries read this way carry a `path`
    but no `resource_id` and no timestamps; use `path` to address them.

    Links are appended to the same page. A link has no file, so its record is
    the resource: it carries a `resource_id` and no `path`. Filter the page to
    one kind with `type`.
    """
    safe = _safe_path(path)
    entries: list[Resource] = []
    if type is None or type in (ResourceType.FILE, ResourceType.FOLDER):
        entity_type, entity_id, engine_type = _file_coords(bot_id, owner_id, bot_repo)
        try:
            listed = await file_svc.list_dir(
                entity_type=entity_type,
                entity_id=entity_id,
                bot_id=bot_id,
                engine_type=engine_type,
                path=safe,
            )
        except httpx.HTTPStatusError as exc:
            # Same normalization the read path does, for the same reason: the
            # baas providers re-raise an upstream 404 rather than answering
            # "nothing there", and that loudness is load-bearing at the device.
            # An absent directory is an ordinary answer here, and the providers
            # that return ``None`` already produce an empty page for it — so
            # without this the same request is a 500 on baas and an empty page
            # everywhere else. Every other status still propagates.
            if exc.response.status_code != 404:
                raise
            listed = []
        for entry in listed or []:
            is_dir = bool(entry.get("is_dir"))
            entry_type = ResourceType.FOLDER if is_dir else ResourceType.FILE
            if type is not None and entry_type != type:
                continue
            entries.append(
                Resource(
                    # No record backs a workspace entry, so there is no id to
                    # report and no source or timestamps to report either — the
                    # device's listing carries none.
                    resource_id="",
                    name=entry.get("name", ""),
                    type=entry_type,
                    size=entry.get("size") if not is_dir else None,
                    # ``ResourceFileService.list_dir`` already returns a
                    # workspace-relative ``path`` (it applies ``_rel_path``
                    # itself) and keeps the engine-view container path under a
                    # separate ``absolute_path``. Recomputing it here would
                    # discard the correct value in favour of a reconstruction.
                    path=entry.get("path", ""),
                )
            )

    if type is None or type == ResourceType.LINK:
        service = factory.create(bot_id=bot_id)
        # Unfiltered on the repo side, then narrowed by the *mapped* type. Two
        # legacy types collapse into openapi LINK — ``LINK`` and the older
        # ``URL`` — so filtering the query on either one alone silently drops
        # the other. FILE rows are excluded here rather than merged: for files
        # the workspace is authoritative, and a row whose bytes are not where it
        # claims must not be reported as a file that exists.
        entries.extend(
            _to_openapi_resource(r)
            for r in service.list_resources()
            if _TYPE_MAP.get(r.resource_type) == ResourceType.LINK
        )

    # Paginated here rather than pushed down: the page spans two sources, one of
    # which is a directory listing with no offset to push a bound into.
    start = (page.page - 1) * page.page_size
    return page_envelope(
        len(entries), entries[start : start + page.page_size], request
    )


@router.get("/check-name", response_model=Envelope[NameCheck])
@envelope_errors
async def check_resource_name(
    owner_id: UserIdDep,
    request: Request,
    # Plain defaults rather than ``Query(...)``: FastAPI still treats them as
    # query parameters, and the handler stays directly callable in tests without
    # a ``Query`` sentinel standing in for a string.
    name: str = "",
    path: str = "",
    type: ResourceType | None = None,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[NameCheck]:
    """Check whether a resource already exists.

    Files and links are addressed differently, so this endpoint takes two
    parameters and at least one is required (400 otherwise):

    - A file or folder is checked by `path`, against the bot's workspace — the
      same question upload asks before writing, answered by the same authority,
      so the two cannot disagree. Send `path`, or send `name` with
      `type=file` or `type=folder` and it is read as a path.
    - A link is checked by `name`, against the link records, because a link has
      no file. This is what you get when you send `name` alone.

    **Not a reliable preflight for creating a link.** A link here is checked
    against a different set of records than the one link creation writes to, so
    a name this endpoint reports as free can still be refused by create with
    409. Treat create's answer as the authoritative one and handle the 409;
    the file check above has no such gap.
    """
    # The link mismatch is real, not a caveat invented for the docstring: this
    # maps openapi LINK to legacy ResourceType.LINK, while `create_url_resource`
    # checks and writes legacy ResourceType.URL — which is what every link
    # created through this surface is. So the check scans a set that holds none
    # of them. Fixing it means deciding whether LINK fans out to both legacy
    # types here (the "URL fan-out" follow-up noted on _OPENAPI_TO_LEGACY_TYPE),
    # which is a behaviour change to the resources contract and not this
    # change's to make. Until then the endpoint says what it actually does.
    #
    # parent_path / exclude_id from the service signature stay off this contract
    # and are passed as None: resources are bot-scoped, so there is no parent
    # path to qualify the name with.
    # Neither addressing mode supplied. Both parameters carry plain defaults so
    # that one may be omitted, which costs FastAPI's required-parameter check —
    # so the "at least one" rule is enforced here instead. Without it the link
    # branch asks the repository about the empty name and answers a cheerful
    # `exists=false` to a request that named no resource at all.
    if not name and not path:
        raise InvalidResourcePathError("name or path is required")
    if path or type == ResourceType.FILE or type == ResourceType.FOLDER:
        safe = _safe_path(path or name)
        if not safe:
            raise InvalidResourcePathError("path is required")
        entity_type, entity_id, engine_type = _file_coords(bot_id, owner_id, bot_repo)
        exists = await file_svc.exists(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            engine_type=engine_type,
            path=safe,
        )
        return envelope(NameCheck(name=safe, exists=exists), request)

    service = factory.create(bot_id=bot_id)
    # owner_id is the request's own ``user_id`` — fail-closed, since neither a
    # missing parameter nor one naming another user reaches this line.
    exists = await service.check_name_exists(
        name=name,
        resource_type=_LegacyType.LINK,
        parent_path=None,
        user_id=owner_id,
    )
    return envelope(NameCheck(name=name, exists=exists), request)


@router.post("", status_code=201, response_model=Envelope[Resource])
@envelope_errors
async def create_resource(
    body: ResourceCreate,
    user_id: UserIdDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Resource]:
    """Create a link resource.

    Only `link` resources are created here, and `url` is required for them. The
    other two kinds have their own endpoints: send a file's bytes to the upload
    endpoint (`type=file` here is refused with 400) and create a folder through
    the mkdir endpoint (`type=folder` here is refused with 501). A name already
    used within the bot is refused (409).
    """
    del user_id  # not-yet-enforced ownership — see list_resources
    if body.type == ResourceType.FILE:
        raise HTTPException(
            status_code=400,
            detail="Use POST /openapi/v1/bots/resources/upload for file resources",
        )
    if body.type == ResourceType.FOLDER:
        raise HTTPException(
            status_code=501,
            detail="Create folder not supported yet (Phase 3)",
        )
    if not body.url:
        raise HTTPException(
            status_code=400, detail="url is required for link resources"
        )

    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    # DuplicateResourceError propagates to @envelope_errors → 409 fixed message
    # (no str(exc) leakage, unlike the prior hand-translation).
    r = await service.create_url_resource(
        name=body.name,
        url=body.url,
        # parent_path intentionally NOT forwarded: the openapi ResourceCreate
        # schema carries `parent_id` (a pending follow-up — its ID-vs-path
        # semantics aren't settled). Passing a half-defined value would risk
        # a wrong-attribute write; link scoping by bot_id is sufficient now.
        parent_path=None,
    )
    return created(_to_openapi_resource(r), request)


@router.post(
    "/upload",
    status_code=201,
    response_model=Envelope[Resource],
    responses=_READ_ONLY_RESPONSE,
)
@envelope_errors
async def upload_resource(
    owner_id: UserIdDep,
    path: str,
    content: Annotated[
        bytes,
        Body(
            media_type="application/octet-stream",
            description="The file's raw bytes, sent as the whole request body. "
            "This is not a multipart form upload — send the bytes unwrapped, "
            "with Content-Type: application/octet-stream.",
        ),
    ],
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[Resource]:
    """Upload a file's raw bytes into the bot's workspace.

    The body is the file itself, not a form: send the bytes with
    `Content-Type: application/octet-stream` and name the file with the `path`
    query parameter.

    `path` is workspace-relative and carries its own directories
    (`docs/spec/a.txt`); intermediate ones are created for you. There is no
    separate name or parent-directory parameter — the directory is part of the
    path, and `path` is the same spelling every other file endpoint uses, so a
    path from a listing can be passed straight back. A path already in use
    within the workspace is refused (409).
    """
    # The write goes through ResourceFileService, the same service the console
    # uses, so both surfaces compose the workspace address identically and
    # cannot drift. owner_id comes from the request's user_id (UserIdDep),
    # fail-closed — mirroring the bots router.
    safe = _safe_path(path)
    if not safe:
        raise InvalidResourcePathError("path is required")
    _reject_read_only(safe)
    entity_type, entity_id, engine_type = _file_coords(bot_id, owner_id, bot_repo)
    # Duplicate detection against the workspace, not the record table. Uploading
    # to an occupied path would otherwise overwrite silently, since the engine's
    # upload is a plain write. Asking the filesystem also fixes the old false
    # positive: two files with the same leaf name in different directories were
    # one row-level ``(name, parent_path)`` collision, and are now two distinct
    # paths.
    #
    # Not atomic with the write, and knowingly so: two uploads racing on the same
    # absent path can both pass here, and last writer wins. That gap is unchanged
    # from the record-table version this replaces — only the authority being asked
    # changed. Closing it needs an exclusive-create on the engine's write API,
    # since ``DeviceFileSystem`` has no conditional-create to make the check and
    # the write one operation; see the spec's known-limitation section.
    if await file_svc.exists(
        entity_type=entity_type,
        entity_id=entity_id,
        bot_id=bot_id,
        engine_type=engine_type,
        path=safe,
    ):
        raise DuplicateResourceError(f"Resource already exists: {safe}")
    try:
        info = await file_svc.upload_file(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            engine_type=engine_type,
            target_dir="",
            filename=safe,
            data=content,
            # ``safe`` already carries its directories, so the structure-preserving
            # branch is the one that must run; the other would flatten it to a
            # basename and silently drop the caller's directories.
            preserve_structure=True,
        )
    except ValueError as exc:
        # Extension allow-list and size cap, raised as bare ValueError by the
        # service. Unmapped by ENVELOPE_ERRORS, so without this it would surface
        # as a 500 — the caller's input was wrong, not the server. The public
        # message is the status phrase by house convention; the real reason is
        # logged here so it is still diagnosable.
        logger.warning("[upload_resource] rejected upload: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        # Device write failure → 502. No record is written below, so a failed
        # upload leaves neither bytes nor a row.
        logger.exception("[upload_resource] device write failed")
        raise HTTPException(status_code=502, detail="Upload storage failed")

    # The record is not decoration: the publish pipeline builds a released bot's
    # manifest from it, so bytes without a row publish as a bot silently missing
    # that file. Reporting 201 and moving on would leave exactly that, and the
    # obvious repair — upload it again — cannot work, because the file is now on
    # disk and the duplicate check answers 409. So the write is rolled back and
    # the request fails: a retry then finds a clean slate and succeeds.
    try:
        await factory.create(bot_id=bot_id).record_uploaded_file(
            path=info["path"],
            size=info.get("size", len(content)),
            user_id=owner_id,
            created_by=owner_id,
        )
    except Exception:
        logger.exception(
            "[upload_resource] record failed; rolling back the file at %s",
            info["path"],
        )
        # A refused rollback is reported by a ``False`` return, not by raising,
        # so catching only exceptions would miss half of it. Both arms land in
        # the same state and get the same log line: the file is on disk with no
        # record, which is what the rollback existed to prevent, and the next
        # upload of this path will 409 against a file the operator has no record
        # of. Nothing is left to try, so it is a log rather than a raise — the
        # 502 below already tells the caller the upload failed.
        try:
            rolled_back = await file_svc.delete(
                entity_type=entity_type,
                entity_id=entity_id,
                bot_id=bot_id,
                engine_type=engine_type,
                path=safe,
            )
        except Exception:
            logger.exception(
                "[upload_resource] rollback failed; %s is on disk with no record",
                info["path"],
            )
        else:
            if not rolled_back:
                logger.error(
                    "[upload_resource] rollback refused; %s is on disk with no record",
                    info["path"],
                )
        raise HTTPException(status_code=502, detail="Upload storage failed")

    # Built from the workspace, not from the row that was just written — the row
    # is the publish pipeline's input, not a source this API reads back. Reading
    # it would make the upload the one file response shaped differently from
    # every other: a listing of this same file reports an empty ``resource_id``
    # and no source or timestamps, and the id echoed here could not address
    # anything anyway, since every file operation is path-addressed and
    # ``GET /{resource_id}`` 404s a file row. ``path`` is the usable handle.
    return created(
        Resource(
            resource_id="",
            name=info["name"],
            type=ResourceType.FILE,
            size=info.get("size", len(content)),
            path=info["path"],
        ),
        request,
    )


# ── file operations, addressed by workspace-relative path ────────────
#
# These are declared before ``/{resource_id}`` so their literal segments win the
# match, exactly as ``/check-name`` and ``/upload`` already do. A file has no
# record id to address it by — the workspace is the source of truth, and a file
# the bot created itself never had a record at all — so ``?path=`` is the
# address, matching the console's own file surface.


async def _read_file_or_404(
    file_svc: ResourceFileService,
    *,
    bot_id: str,
    owner_id: str,
    bot_repo: BotRepository,
    path: str,
) -> bytes:
    """Bytes at ``path``, or 404. Shared by download and preview."""
    safe = _safe_path(path)
    if not safe:
        raise InvalidResourcePathError("path is required")
    entity_type, entity_id, engine_type = _file_coords(bot_id, owner_id, bot_repo)
    try:
        content = await file_svc.read_file(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            engine_type=engine_type,
            path=safe,
            enforce_download_limit=True,
        )
    except DeviceFileTooLargeError as exc:
        # Two distinct classes share the name ``FileTooLargeError``: the device
        # filesystem raises its own, and only the resources one is in
        # ENVELOPE_ERRORS. Without this the documented 413 escapes as a 500 —
        # ENVELOPE_ERRORS maps concrete types, so a same-named sibling is not a
        # match. Re-raised as the mapped type rather than adding a second entry,
        # so there is one answer for "too large" regardless of who noticed.
        raise FileTooLargeError(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        # The baas providers deliberately re-raise the upstream status rather
        # than returning ``None`` for a missing file
        # (``baas_device_filesystem.py:68``) — a swallowed 401 once returned
        # empty content that callers read as "file gone", silently dropping
        # promoted files, so that loudness is load-bearing and must not be
        # changed at the device. It is only *here* that a 404 stops being a
        # failure and becomes an ordinary answer: this route documents 404 for a
        # path that is not there. Every other status stays unmapped and surfaces
        # as a 500, which is what an upstream fault is.
        if exc.response.status_code != 404:
            raise
        raise HTTPException(status_code=404, detail="Resource not found") from exc
    # ``None`` is absent. ``b""`` is a file that exists and happens to be empty,
    # and it is served as one: an empty file appears in a listing, so answering
    # 404 for it would be the workspace and the API disagreeing about what
    # exists — the exact divergence this change removes. The legacy contract
    # conflated the two, which is a reason to fix it here, not to copy it.
    if content is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return content


@router.get(
    "/download",
    responses={
        200: {"content": {"application/octet-stream": {}}},
        **_TOO_LARGE_RESPONSE,
    },
)
@envelope_errors
async def download_file(
    owner_id: UserIdDep,
    path: str,
    # Required by ``@envelope_errors`` to locate the request: without it the
    # decorator cannot build an error envelope and re-raises instead, so a
    # rejected path would surface as a 500 rather than a 400. The success path
    # still returns raw bytes, not an envelope.
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Response:
    """Download a file from the bot's workspace, addressed by `path`.

    The response is the raw bytes — this is one of the two endpoints on this
    surface that does not wrap its answer in the standard envelope. A path with
    no file behind it answers 404, and a file too large for the provider to
    serve answers 413.
    """
    # Replaces the former /{resource_id}/download: a record id cannot address a
    # file the bot created itself, and the record is no longer what decides
    # existence.
    content = await _read_file_or_404(
        file_svc, bot_id=bot_id, owner_id=owner_id, bot_repo=bot_repo, path=path
    )
    return Response(content=content, media_type="application/octet-stream")


@router.get(
    "/preview",
    response_model=Envelope[Preview],
    responses=_TOO_LARGE_RESPONSE,
)
@envelope_errors
async def preview_file(
    owner_id: UserIdDep,
    path: str,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[Preview]:
    """Read a file's content as text, addressed by `path`.

    Capped at 1 MB — content over that is refused with 413 rather than
    truncated, so you are never handed a prefix you might mistake for the whole
    file. Bytes that are not valid UTF-8 are replaced rather than refused, so a
    mostly-text file still previews. A path with no file behind it answers 404.
    """
    content = await _read_file_or_404(
        file_svc, bot_id=bot_id, owner_id=owner_id, bot_repo=bot_repo, path=path
    )
    if len(content) > _PREVIEW_MAX_BYTES:
        raise FileTooLargeError(
            f"File too large for preview (max {_PREVIEW_MAX_BYTES} bytes)"
        )
    return envelope(
        Preview(
            resource_id="",
            content_type="application/octet-stream",
            # ``replace`` rather than raising: a preview of a mostly-text file
            # with a stray byte is useful; a 500 is not.
            content=content.decode("utf-8", errors="replace"),
        ),
        request,
    )


@router.delete(
    "",
    response_model=Envelope[Deleted],
    responses=_READ_ONLY_RESPONSE,
)
@envelope_errors
async def delete_file(
    owner_id: UserIdDep,
    path: str,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[Deleted]:
    """Delete a file or directory from the bot's workspace, addressed by `path`.

    The workspace decides what exists, so a path with nothing behind it answers
    404. Some paths are read-only and are refused with 403: dotfiles, and the
    identity files at the workspace root. Links are not deleted here — they have
    no file, and are deleted by their `resource_id` instead.
    """
    # A missing record is not an error: a file the bot created itself never had
    # one.
    safe = _safe_path(path)
    if not safe:
        raise InvalidResourcePathError("path is required")
    # Same read-only policy the console's delete enforces
    # (``adapters/http/resources/file_router.py:381``) — dotfiles and the
    # workspace-root identity files. Addressing by path is what makes these
    # reachable at all: they never had a resource record, so the old
    # id-addressed delete could not name them and needed no guard.
    if is_readonly(safe):
        raise HTTPException(status_code=403, detail="Cannot delete read-only file")
    entity_type, entity_id, engine_type = _file_coords(bot_id, owner_id, bot_repo)
    if not await file_svc.exists(
        entity_type=entity_type,
        entity_id=entity_id,
        bot_id=bot_id,
        engine_type=engine_type,
        path=safe,
    ):
        raise HTTPException(status_code=404, detail="Resource not found")
    # Record first, file second — the reverse order cannot recover. Deleting the
    # file first and then failing to drop the record leaves the manifest pointing
    # at a path with no bytes, and the retry is refused 404 because the file is
    # already gone, so nothing can clear it. This way a record failure changes
    # nothing and the retry works; a file failure leaves a record already gone,
    # which the retry tolerates (a missing record is not an error) while it
    # retries the file.
    await factory.create(bot_id=bot_id).delete_file_record(path=safe)
    # The providers report a refused delete by returning False rather than
    # raising, so discarding this would answer ``deleted=true`` over a file
    # still sitting in the workspace. 502 rather than the console's 404: the
    # existence check above already ran, so False here means the device refused,
    # not that the path was absent. The record is gone by now, which the retry
    # tolerates — a missing record is not an error — while it retries the file.
    if not await file_svc.delete(
        entity_type=entity_type,
        entity_id=entity_id,
        bot_id=bot_id,
        engine_type=engine_type,
        path=safe,
    ):
        raise HTTPException(status_code=502, detail="Delete storage failed")
    return deleted_envelope(request)


@router.post(
    "/mkdir",
    status_code=201,
    response_model=Envelope[Resource],
    responses=_READ_ONLY_RESPONSE,
)
@envelope_errors
async def create_directory(
    owner_id: UserIdDep,
    path: str,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[Resource]:
    """Create a directory in the bot's workspace, addressed by `path`.

    Intermediate directories are created for you. The directory exists in the
    workspace and is reported by the listing endpoint, but it has no resource
    record, so the `resource_id` in the response is empty and the per-resource
    endpoints do not address it — use `path` for everything a directory needs.
    Creating one through the create endpoint with `type=folder` is refused
    (501); this is the endpoint for it.
    """
    # Deliberately no FOLDER record: giving directories records would
    # reintroduce exactly the record-vs-filesystem divergence this group's move
    # to the workspace removes.
    safe = _safe_path(path)
    if not safe:
        raise InvalidResourcePathError("path is required")
    _reject_read_only(safe)
    entity_type, entity_id, engine_type = _file_coords(bot_id, owner_id, bot_repo)
    await file_svc.create_directory(
        entity_type=entity_type,
        entity_id=entity_id,
        bot_id=bot_id,
        engine_type=engine_type,
        path=safe,
    )
    return created(
        Resource(
            resource_id="",
            name=safe.rsplit("/", 1)[-1],
            type=ResourceType.FOLDER,
            path=safe,
        ),
        request,
    )


@router.get("/{resource_id}", response_model=Envelope[Resource])
@envelope_errors
async def get_resource(
    resource_id: str,
    user_id: UserIdDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Resource]:
    """Get a link resource, addressed by `resource_id`.

    Links only. A file's identifier answers 404: a file's address is its path,
    so list it through the collection endpoint and read it through download or
    preview.
    """
    # Serving a file from its record would report size and name from a row that
    # nothing keeps in step with the workspace — the divergence this group's
    # move to the workspace removes.
    del user_id  # not-yet-enforced ownership — see list_resources
    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    # NOTE: ``get_resource`` on the concrete service is SYNC (unlike
    # ``check_name_exists`` which is async) — do NOT `await` it.
    r = service.get_resource(resource_id)
    if r is None or r.resource_type == _LegacyType.FILE:
        raise HTTPException(status_code=404, detail="Resource not found")
    return envelope(_to_openapi_resource(r), request)


@router.put("/{resource_id}", response_model=Envelope[Resource])
@envelope_errors
async def update_resource(
    resource_id: str,
    body: ResourceUpdate,
    user_id: UserIdDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Resource]:
    """Rename a link resource or change its target URL.

    Links only, addressed by `resource_id`. A file's identifier answers 404 —
    files live in the workspace and are not renamed through this endpoint. A
    name or URL already used within the bot is refused (409).
    """
    # link_type is intentionally not exposed on the openapi contract.
    # ValueError from the service (not found / url clash) → 409 Conflict, per
    # legacy + create parity.
    #
    # A file id 404s, completing what GET and DELETE /{resource_id} already do.
    # `update_link_resource` checks no type, so a stale file id would otherwise
    # rename a FILE row that nothing reads — the response would even show the
    # new name, while the workspace, which is what every file response is
    # actually built from, is untouched.
    del user_id  # not-yet-enforced ownership — see list_resources
    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    existing = service.get_resource(resource_id)
    if existing is not None and existing.resource_type == _LegacyType.FILE:
        raise HTTPException(status_code=404, detail="Resource not found")
    # ResourceNotFoundError (404) / DuplicateResourceError (409) propagate to
    # @envelope_errors with fixed messages — no str(exc) leakage. The prior
    # hand-translation also wrongly mapped not-found → 409; this fixes that.
    r = await service.update_link_resource(
        resource_id=resource_id,
        name=body.name,
        url=body.url,
    )
    return envelope(_to_openapi_resource(r), request)

@router.delete("/{resource_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_resource(
    resource_id: str,
    owner_id: UserIdDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Deleted]:
    """Delete a link resource, addressed by `resource_id`.

    Links only. A link has no file, so the record is the resource and its
    identifier is the only address it can have. Files and folders live in the
    workspace and are deleted by path instead, through the collection endpoint's
    delete. A file's identifier sent here answers 404 — which is also the right
    answer for an identifier left over from before files moved to the workspace.

    The bot needs no device bound for this: nothing on a device is touched.
    """
    # The former unconditional resolve_for_bot raised DeviceNotBoundError (409)
    # on an unbound bot even for a link; removing the file branch resolves it.
    service = factory.create(bot_id=bot_id)
    # Refuse a FILE row explicitly. ``delete_resource`` would otherwise accept
    # one, skip the device because ``device_fs`` is None, soft-delete the row and
    # report success — so a stale file id would still "work" while leaving the
    # workspace file in place, which is precisely the record-says-one-thing,
    # workspace-says-another divergence this change exists to remove. 404 rather
    # than a distinct status: to this route a file id is simply not a resource it
    # addresses, and the file's own address is ``DELETE ""?path=``.
    existing = service.get_resource(resource_id)
    if existing is not None and existing.resource_type == _LegacyType.FILE:
        raise HTTPException(status_code=404, detail="Resource not found")
    ok = await service.delete_resource(resource_id, device_fs=None)
    if not ok:
        raise HTTPException(status_code=404, detail="Resource not found")
    return deleted_envelope(request)
