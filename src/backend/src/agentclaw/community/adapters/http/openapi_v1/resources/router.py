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

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response

from agentclaw.community.api.resource_service import ResourceServiceFactoryProtocol
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
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
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.resources.service import (
    DuplicateResourceError,
    FileTooLargeError,
    InvalidResourcePathError,
)
from agentclaw.community.core.services.resource_file_service import ResourceFileService
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


def _entry_path(listed_dir: str, entry: dict) -> str:
    """Workspace-relative path of a listing entry.

    The device reports ``relative_path`` relative to the *listed* directory, not
    to the workspace root — listing ``a/b`` yields ``c.txt`` — so it has to be
    rejoined with the directory that was listed. teclaw returns no
    ``relative_path`` at all, hence the fallback to the entry name, which is
    equivalent for the non-recursive listings this endpoint does. Mirrors
    ``resource_file_service._rel_path``.

    The entry's own ``path`` is deliberately unused: it is the engine-view
    absolute container path (``/home/admin/.aicoding/workspace/...``) and must
    not cross a public API.
    """
    leaf = entry.get("relative_path") or entry.get("name", "")
    return f"{listed_dir}/{leaf}" if listed_dir else leaf


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

    Files and folders come from the **workspace**, never from records. That is
    what makes a file the bot produced itself a first-class resource: it has no
    record and never will, and a record-backed listing simply could not see it.
    It also removes the divergence the other direction — a record whose bytes are
    not where it claims can no longer be reported as a file that exists.

    Links are the exception, and not an inconsistency: a link has no file and no
    presence on any device, so the record *is* the resource. They are read from
    the repo and appended.

    ``path`` selects the directory (empty = workspace root). Listing is
    non-recursive, which bounds the device round trip this endpoint now makes.
    """
    safe = _safe_path(path)
    entries: list[Resource] = []
    if type is None or type in (ResourceType.FILE, ResourceType.FOLDER):
        entity_type, entity_id, engine_type = _file_coords(bot_id, owner_id, bot_repo)
        listed = await file_svc.list_dir(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            engine_type=engine_type,
            path=safe,
        )
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
                    path=_entry_path(safe, entry),
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
    name: str,
    owner_id: UserIdDep,
    request: Request,
    type: ResourceType | None = None,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[NameCheck]:
    """Check whether a resource name is available.

    ``user_id`` is the request's own required parameter, forwarded to the
    service — the wiring the older note here said was still pending.
    ``parent_path`` and ``exclude_id`` from the legacy signature remain
    unexposed on this contract and are passed as None: resources are bot-scoped,
    so there is no parent path to qualify the name with.
    """
    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    # Map openapi ResourceType → legacy ResourceType enum the slim service
    # expects. When the caller omits ``type``, default to FILE (matches the
    # legacy handler's most common case — the openapi check-name call shape
    # has no FOLDER equivalent).
    legacy_type = _legacy_type_for(type) or _LegacyType.FILE
    # owner_id is the request's own ``user_id`` — fail-closed, since neither a
    # missing parameter nor one naming another user reaches this line. The slim
    # service signature REQUIRES both keyword args (no defaults), so parent_path
    # is passed explicitly as None rather than omitted.
    exists = await service.check_name_exists(
        name=name,
        resource_type=legacy_type,
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
    """Create a resource (file placeholder, link, or folder).

    Phase 1: only LINK supported. FILE → use POST /upload (Phase 3);
    FOLDER → create_directory (Phase 3, device_fs branch). Duplicate name
    surfaces as ValueError from the service → 409 Conflict (legacy parity).
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


@router.post("/upload", status_code=201, response_model=Envelope[Resource])
@envelope_errors
async def upload_resource(
    owner_id: UserIdDep,
    path: str,
    content: Annotated[bytes, Body(media_type="application/octet-stream")],
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[Resource]:
    """Upload a file's raw bytes into the bot's workspace.

    ``path`` is workspace-relative and carries its own directories
    (``docs/spec/a.txt``); intermediate ones are created by the engine. There is
    no separate name or parent-directory parameter — the directory is part of the
    path, and ``path`` is the same spelling every other file endpoint uses.

    The write goes through ``ResourceFileService``, the same service the console
    uses, so both surfaces compose the workspace address identically and cannot
    drift. ``owner_id`` comes from the request's ``user_id`` (``UserIdDep``),
    fail-closed — mirroring the bots router.
    """
    safe = _safe_path(path)
    if not safe:
        raise InvalidResourcePathError("path is required")
    entity_type, entity_id, engine_type = _file_coords(bot_id, owner_id, bot_repo)
    # Duplicate detection against the workspace, not the record table. Uploading
    # to an occupied path would otherwise overwrite silently, since the engine's
    # upload is a plain write. Asking the filesystem also fixes the old false
    # positive: two files with the same leaf name in different directories were
    # one row-level ``(name, parent_path)`` collision, and are now two distinct
    # paths.
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

    # The bytes are in the workspace now, so the file exists whatever happens
    # next: the record carries only what the filesystem cannot know (uploader,
    # upload time, upload-vs-bot-created) and what the publish pipeline reads.
    # Failing the request here would report "upload failed" for a file that is
    # demonstrably there, and a retry would then hit the duplicate check.
    data = Resource(
        resource_id="",
        name=info["name"],
        type=ResourceType.FILE,
        source="upload",
        size=info.get("size", len(content)),
        path=info["path"],
    )
    try:
        record = await factory.create(bot_id=bot_id).record_uploaded_file(
            path=info["path"],
            size=info.get("size", len(content)),
            user_id=owner_id,
            created_by=owner_id,
        )
    except Exception:
        logger.exception(
            "[upload_resource] enrichment record failed; file is written at %s",
            info["path"],
        )
    else:
        data = _to_openapi_resource(record)
        data.path = info["path"]
    return created(data, request)


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
    content = await file_svc.read_file(
        entity_type=entity_type,
        entity_id=entity_id,
        bot_id=bot_id,
        engine_type=engine_type,
        path=safe,
        enforce_download_limit=True,
    )
    # ``None`` is absent; ``b""`` is a file that exists but has no bytes, which
    # the legacy contract also treated as nothing to serve.
    if not content:
        raise HTTPException(status_code=404, detail="Resource not found")
    return content


@router.get("/download", responses={200: {"content": {"application/octet-stream": {}}}})
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
    """Stream a file's bytes from the bot's workspace, addressed by path.

    Raw bytes, not an envelope — the body is the file. Replaces the former
    ``/{resource_id}/download``: a record id cannot address a file the bot
    created itself, and the record is no longer what decides existence.
    """
    content = await _read_file_or_404(
        file_svc, bot_id=bot_id, owner_id=owner_id, bot_repo=bot_repo, path=path
    )
    return Response(content=content, media_type="application/octet-stream")


@router.get("/preview", response_model=Envelope[Preview])
@envelope_errors
async def preview_file(
    owner_id: UserIdDep,
    path: str,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[Preview]:
    """A file's content as text, addressed by path.

    Capped at 1 MB (legacy parity) — over that is a 413, not a truncated preview,
    so a caller is never handed a prefix it might mistake for the whole file.
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


@router.delete("", response_model=Envelope[Deleted])
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
    """Delete a file or directory from the bot's workspace, addressed by path.

    The workspace is authoritative, so the file goes first and any matching
    record follows. A record that is not there is not an error — a file the bot
    created never had one — but a file that is not there is a 404.
    """
    safe = _safe_path(path)
    if not safe:
        raise InvalidResourcePathError("path is required")
    entity_type, entity_id, engine_type = _file_coords(bot_id, owner_id, bot_repo)
    if not await file_svc.exists(
        entity_type=entity_type,
        entity_id=entity_id,
        bot_id=bot_id,
        engine_type=engine_type,
        path=safe,
    ):
        raise HTTPException(status_code=404, detail="Resource not found")
    await file_svc.delete(
        entity_type=entity_type,
        entity_id=entity_id,
        bot_id=bot_id,
        engine_type=engine_type,
        path=safe,
    )
    try:
        await factory.create(bot_id=bot_id).delete_file_record(path=safe)
    except Exception:
        logger.exception(
            "[delete_file] record cleanup failed; file is gone at %s", safe
        )
    return deleted_envelope(request)


@router.post("/mkdir", status_code=201, response_model=Envelope[Resource])
@envelope_errors
async def create_directory(
    owner_id: UserIdDep,
    path: str,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[Resource]:
    """Create a directory in the bot's workspace.

    Physical only — no FOLDER record is written, and ``POST ""`` with
    ``type=FOLDER`` still returns 501. Directories exist on the filesystem and
    are reported by listing it; giving them records would reintroduce exactly
    the record-vs-filesystem divergence this change removes.
    """
    safe = _safe_path(path)
    if not safe:
        raise InvalidResourcePathError("path is required")
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
    """Get a resource."""
    del user_id  # not-yet-enforced ownership — see list_resources
    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    # NOTE: ``get_resource`` on the concrete service is SYNC (unlike
    # ``check_name_exists`` which is async) — do NOT `await` it.
    r = service.get_resource(resource_id)
    if r is None:
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
    """Update a resource (link rename / url change).

    Phase 3a: link only; ``link_type`` is intentionally not exposed on the
    openapi contract. ValueError from the service (not found / url clash)
    → 409 Conflict, per legacy + create parity.
    """
    del user_id  # not-yet-enforced ownership — see list_resources
    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
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
    """Delete a **link** resource by record id.

    Files are no longer addressed this way — they live in the workspace and are
    deleted through ``DELETE ""?path=``. A link has no file, so the record is
    the resource and the record id is the only address it can have. A file id
    sent here resolves to nothing and 404s, which is also the right answer for
    a stale id from before the change.

    No device is touched, so this must not require a bound one: the former
    unconditional ``resolve_for_bot`` raised DeviceNotBoundError (409) on an
    unbound bot even for a link, which is the follow-up that removing the file
    branch resolves.
    """
    service = factory.create(bot_id=bot_id)
    ok = await service.delete_resource(resource_id, device_fs=None)
    if not ok:
        raise HTTPException(status_code=404, detail="Resource not found")
    return deleted_envelope(request)
