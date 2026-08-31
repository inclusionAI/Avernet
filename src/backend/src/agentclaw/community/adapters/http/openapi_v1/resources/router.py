"""Resources group — ``/openapi/v1/bots/{bot_id}/resources``.

The bot's workspace files and folders. The engine is the source of truth: every
handler resolves against the workspace, and every entry is addressed by its
workspace-relative ``path``. The storage location is never exposed.

**There are no record ids on this surface.** ``#1001`` made the filesystem
authoritative for files, which left ``resource_id`` present but permanently
empty on every file response, and three id-addressed routes that only ever
resolved link records. Links are no longer part of this group, so the id is gone
from the contract rather than reported as ``""`` — an empty string standing in
for "no id" is a sentinel pretending to be an address, and a caller cannot tell
it from a real one until it fails.

⚠️ STATUS: definition-only / NOT PUBLIC-READY. The handlers exercise the real
services at the integration level, but this surface is gated on the auth
workstream before it is exposed to any external tenant: ``require_principal`` is
still a ``None`` stub, so the gateway's signed-Principal seam is not in place
yet. Do NOT expose to external callers until that lands (see
``openapi_v1/dependencies.py`` and the cross-team tenant isolation track in
``src/backend/docs/openapi-v1/README.zh-CN.md``).

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

Records are still written on upload, and that is not a contradiction of the
above: the publish pipeline builds a released bot's manifest from them, so bytes
with no row publish as a bot silently missing that file. The row is that
pipeline's input, never a source this API reads back. "Files have no id in the
API" is not "files have no record".
"""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Body, HTTPException, Query, Request, Response

from agentclaw.community.api.resource_service import ResourceServiceFactoryProtocol
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
    ErrorEnvelope,
    Page,
    PageParamsDep,
    error_example,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted as deleted_envelope,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.core.devices.services.device_filesystem import (
    FileTooLargeError as DeviceFileTooLargeError,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.resources.service import (
    DuplicateResourceError,
    FileTooLargeError,
)
from agentclaw.community.core.services.resource_file_service import (
    ResourceFileService,
    is_readonly,
    is_write_forbidden,
    require_workspace_path,
    resource_coords_from_record,
    safe_workspace_path,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

from .schemas import FileEntry, Preview, ResourceType
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

logger = get_logger()

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
        **error_example(413, "File too large for preview"),
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
        **error_example(403, "Forbidden"),
    },
}


#: The workspace-path rules, which live in ``core`` because they are statements
#: about the workspace rather than about HTTP — and because manifest apply
#: enforces the same ones with no request to hang them on. Bound here under the
#: names this module has always used, so every call site below reads as it did;
#: these are the core functions themselves, not wrappers, which is what
#: ``test_config_surface.py`` pins with ``is``.
_safe_path = safe_workspace_path
_require_path = require_workspace_path
_file_coords = resource_coords_from_record


def _reject_read_only(safe: str) -> None:
    """Refuse a path the read-only policy protects, with the console's 403.

    The *decision* is :func:`is_write_forbidden` in ``core``; this is the
    protocol half, and it stays here deliberately. Core raising a domain error
    that this module mapped back would have to reproduce this body exactly, and
    "reproduced exactly" is a claim to verify where "never moved" is a fact. The
    rule this feature is applying — an adapter may map errors, it may not own
    policy — puts the line right here.
    """
    if is_write_forbidden(safe):
        raise HTTPException(
            status_code=403, detail="Cannot write to a read-only path"
        )


def _to_file_entry(entry: dict[str, Any]) -> FileEntry:
    """Map a ``ResourceFileService.list_dir`` entry to the public schema.

    ``list_dir`` already returns a workspace-relative ``path`` (it applies
    ``_rel_path`` itself) and keeps the engine-view container path under a
    separate ``absolute_path``. Recomputing the former here would discard the
    correct value in favour of a reconstruction; reporting the latter would leak
    the container's storage layout.
    """
    is_dir = bool(entry.get("is_dir"))
    return FileEntry(
        path=entry.get("path", ""),
        name=entry.get("name", ""),
        type=ResourceType.FOLDER if is_dir else ResourceType.FILE,
        size=entry.get("size") if not is_dir else None,
    )


async def _list_dir_or_empty(
    file_svc: ResourceFileService,
    *,
    bot_id: str,
    owner_id: str,
    bot_repo: BotRepository,
    path: str,
) -> list[dict[str, Any]]:
    """One directory's entries, treating an absent directory as empty.

    The baas providers re-raise an upstream 404 rather than answering "nothing
    there", and that loudness is load-bearing at the device
    (``baas_device_filesystem.py:68``). It is only here that a 404 stops being a
    failure: the providers that return ``None`` already produce an empty listing
    for an absent directory, so without this the same request is a 500 on baas
    and an empty page everywhere else. Every other status still propagates.
    """
    coords = _file_coords(bot_id, owner_id, bot_repo)
    entity_type, entity_id, engine_type = (
        coords.entity_type, coords.entity_id, coords.engine_type
    )
    try:
        listed = await file_svc.list_dir(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            engine_type=engine_type,
            path=path,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        return []
    return listed or []


router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/resources", tags=["resources"], route_class=PublicAPIRoute)

#: The query parameter addressing a file or folder, documented once. Kept as an
#: Annotated default so handlers stay directly callable in tests.
FilePathQuery = Annotated[
    str,
    Query(
        description="Workspace-relative path of the file or folder, e.g. "
        "'docs/spec/a.txt' — exactly as returned in a listing entry's "
        "`path`. A leading slash is tolerated; '..' segments are refused "
        "(400)."
    ),
]


@router.get("", response_model=Envelope[Page[FileEntry]])
@envelope_errors
async def list_resources(
    page: PageParamsDep,
    owner_id: UserIdDep,
    request: Request,
    bot_id: BotIdPath,
    path: str = Query(
        "", description="Directory to list, relative to the workspace root."
    ),
    type: Annotated[
        ResourceType | None,
        Query(
            description="Filter: 'file' lists only files, 'folder' only "
            "directories. Omit for both."
        ),
    ] = None,
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[Page[FileEntry]]:
    """List a directory of the bot's workspace.

    Entries come from the **workspace**, never from records. That is what makes
    a file the bot produced itself a first-class resource: it has no record and
    never will, and a record-backed listing simply could not see it. It also
    removes the divergence the other direction — a record whose bytes are not
    where it claims can no longer be reported as a file that exists.

    The path parameter selects the directory (empty = workspace root). Listing
    is non-recursive, which bounds the device round trip this endpoint makes.

    Paging is applied by this server over the whole directory, because the
    engine's listing API takes no page, limit or cursor: `page_size` bounds the
    response, not the work. Requesting a later page costs exactly what the
    first one costs.
    """
    safe = _safe_path(path)
    listed = await _list_dir_or_empty(
        file_svc, bot_id=bot_id, owner_id=owner_id, bot_repo=bot_repo, path=safe
    )
    entries = [_to_file_entry(entry) for entry in listed]
    if type is not None:
        entries = [entry for entry in entries if entry.type == type]

    # Sliced here rather than pushed down, and that is only sound because the
    # listing is non-recursive: one directory is a bounded fetch, so holding it
    # whole to serve a page of it is proportionate. A ``recursive=true`` option
    # would break that assumption — it would pull an entire workspace into
    # memory per request — so adding one means revisiting this, not just adding
    # a parameter. See the engine's ``ListDirRequest``, which has no paging
    # fields to push a bound into.
    start = (page.page - 1) * page.page_size
    return page_envelope(
        len(entries), entries[start : start + page.page_size], request
    )


@router.get("/stat", response_model=Envelope[FileEntry])
@envelope_errors
async def stat_resource(
    owner_id: UserIdDep,
    path: FilePathQuery,
    request: Request,
    bot_id: BotIdPath,
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[FileEntry]:
    """One file or folder's metadata, addressed by path.

    Answers the questions a listing answers, for a single entry: whether it is
    there at all (404 if not), whether it is a file or a folder, and how big it
    is. The workspace root has no entry of its own, so `path` is required here
    even though the listing accepts an empty one.

    Entries the listing hides — dotfiles, and the workspace-root identity files
    — are 404 here too, so the two surfaces agree on what exists.
    """
    # Resolved by listing the parent and picking the entry out, rather than by a
    # dedicated device call: it is the same seam the listing reads, so stat and
    # list cannot report different types or sizes for the same file. It also
    # needs no new provider method — ``DeviceFileSystem`` has no stat, and
    # adding one to three providers to save a filter would be the larger change.
    safe = _require_path(path)
    parent = safe.rsplit("/", 1)[0] if "/" in safe else ""
    listed = await _list_dir_or_empty(
        file_svc, bot_id=bot_id, owner_id=owner_id, bot_repo=bot_repo, path=parent
    )
    for entry in listed:
        if entry.get("path") == safe:
            return envelope(_to_file_entry(entry), request)
    raise HTTPException(status_code=404, detail="Resource not found")


@router.post(
    "/upload",
    status_code=201,
    response_model=Envelope[FileEntry],
    responses=_READ_ONLY_RESPONSE,
)
@envelope_errors
async def upload_resource(
    owner_id: UserIdDep,
    path: FilePathQuery,
    content: Annotated[bytes, Body(media_type="application/octet-stream")],
    request: Request,
    bot_id: BotIdPath,
    overwrite: Annotated[
        bool,
        Query(
            description="Replace the file if the path is already occupied. "
            "Defaults to false, which answers 409 instead. Has no effect on a "
            "free path."
        ),
    ] = False,
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[FileEntry]:
    """Upload a file's raw bytes into the bot's workspace.

    The body is the file's raw bytes (content type application/octet-stream),
    not a multipart form. The path is workspace-relative and carries its own
    directories ('docs/spec/a.txt'); intermediate ones are created as needed
    — there is no separate name or parent-directory parameter. An occupied
    path answers 409 unless `overwrite` is set; the size limit is 500 MB, and
    only common document, code, image and archive extensions are accepted
    (400 otherwise).
    """
    # The write goes through ResourceFileService, the same service the console
    # uses, so both surfaces compose the workspace address identically and
    # cannot drift. owner_id comes from the request's user_id (UserIdDep),
    # fail-closed — mirroring the bots router.
    safe = _require_path(path)
    _reject_read_only(safe)
    coords = _file_coords(bot_id, owner_id, bot_repo)
    entity_type, entity_id, engine_type = (
        coords.entity_type, coords.entity_id, coords.engine_type
    )
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
    occupied = await file_svc.exists(
        entity_type=entity_type,
        entity_id=entity_id,
        bot_id=bot_id,
        engine_type=engine_type,
        path=safe,
    )
    if occupied and not overwrite:
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
    # that file.
    try:
        if occupied:
            # Replacing, so the prior row for this path goes first:
            # ``record_uploaded_file`` always inserts, and a second row for one
            # path would publish the file twice in the manifest. Dropping it is
            # safe to do unconditionally on this branch — ``delete_file_record``
            # reports "nothing matched" with a ``False`` rather than raising, and
            # a bot-created file being overwritten never had a row to begin with.
            #
            # This is a *reduction*, not a guarantee, and the difference matters.
            # The drop and the insert are two statements with no lock between
            # them, so two overwrites racing on one path can both drop, then both
            # insert, and leave two live rows — the publish pipeline would list
            # the file twice. Without the drop the same race leaves three, so
            # this strictly improves it, but it does not close it.
            #
            # It is the same race the duplicate check above already documents,
            # one table down: concurrent *fresh* uploads to an absent path both
            # pass ``exists``, both write, and both insert, which is the
            # pre-existing behaviour this branch inherits rather than introduces.
            # Closing it properly needs a uniqueness constraint on
            # ``(bolt_id, path)`` plus an upsert in the repository — a DDL on
            # ``ac_resource``, a table the console and the publish pipeline share.
            # That has to be deployed before code that depends on it (see the
            # module docstring's Phase 0 note on ``avernet_tenant``: code first /
            # DDL later breaks bot reads), so it is not an adapter-level fix and
            # is deliberately not attempted here.
            await factory.create(bot_id=bot_id).delete_file_record(path=safe)
        await factory.create(bot_id=bot_id).record_uploaded_file(
            path=info["path"],
            size=info.get("size", len(content)),
            user_id=owner_id,
            created_by=owner_id,
        )
    except Exception:
        logger.exception(
            "[upload_resource] record failed for %s", info["path"]
        )
        if occupied:
            # No rollback when replacing, deliberately. The prior bytes are
            # already gone — that is what the caller asked for — so deleting the
            # file now would destroy content instead of restoring it. The retry
            # is the repair here and it is not blocked: an overwrite does not
            # 409, so calling it again rewrites the bytes and writes the row.
            logger.error(
                "[upload_resource] %s was replaced but has no record; "
                "retry the overwrite to restore it",
                info["path"],
            )
            raise HTTPException(status_code=502, detail="Upload storage failed")
        # A fresh upload rolls back instead, because its retry *is* blocked:
        # reporting 201 would leave bytes with no row, and the obvious repair —
        # upload it again — cannot work, since the file is now on disk and the
        # duplicate check answers 409. Rolling the write back gives the retry a
        # clean slate.
        #
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
    # is the publish pipeline's input, not a source this API reads back.
    return created(
        FileEntry(
            path=info["path"],
            name=info["name"],
            type=ResourceType.FILE,
            size=info.get("size", len(content)),
        ),
        request,
    )


async def _read_file_or_404(
    file_svc: ResourceFileService,
    *,
    bot_id: str,
    owner_id: str,
    bot_repo: BotRepository,
    path: str,
) -> bytes:
    """Bytes at ``path``, or 404. Shared by download and preview."""
    safe = _require_path(path)
    coords = _file_coords(bot_id, owner_id, bot_repo)
    entity_type, entity_id, engine_type = (
        coords.entity_type, coords.entity_id, coords.engine_type
    )
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
    path: FilePathQuery,
    # Required by ``@envelope_errors`` to locate the request: without it the
    # decorator cannot build an error envelope and re-raises instead, so a
    # rejected path would surface as a 500 rather than a 400. The success path
    # still returns raw bytes, not an envelope.
    request: Request,
    bot_id: BotIdPath,
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Response:
    """Stream a file's bytes from the bot's workspace, addressed by path.

    Raw bytes, not an envelope — the body is the file.
    """
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
    path: FilePathQuery,
    request: Request,
    bot_id: BotIdPath,
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
            path=_safe_path(path),
            content_type="application/octet-stream",
            # ``replace`` rather than raising: a preview of a mostly-text file
            # with a stray byte is useful; a 500 is not.
            content=content.decode("utf-8", errors="replace"),
        ),
        request,
    )


@router.post(
    "/mkdir",
    status_code=201,
    response_model=Envelope[FileEntry],
    responses=_READ_ONLY_RESPONSE,
)
@envelope_errors
async def create_directory(
    owner_id: UserIdDep,
    path: FilePathQuery,
    request: Request,
    bot_id: BotIdPath,
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[FileEntry]:
    """Create a directory in the bot's workspace, addressed by path.

    Intermediate directories are created as needed. Directories exist on the
    workspace filesystem only and are reported by listing it — they have no
    record.
    """
    # No FOLDER record is written: giving directories records would reintroduce
    # exactly the record-vs-filesystem divergence this change removed.
    safe = _require_path(path)
    _reject_read_only(safe)
    coords = _file_coords(bot_id, owner_id, bot_repo)
    entity_type, entity_id, engine_type = (
        coords.entity_type, coords.entity_id, coords.engine_type
    )
    await file_svc.create_directory(
        entity_type=entity_type,
        entity_id=entity_id,
        bot_id=bot_id,
        engine_type=engine_type,
        path=safe,
    )
    return created(
        FileEntry(
            path=safe,
            name=safe.rsplit("/", 1)[-1],
            type=ResourceType.FOLDER,
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
    path: FilePathQuery,
    request: Request,
    bot_id: BotIdPath,
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> Envelope[Deleted]:
    """Delete a file or directory from the bot's workspace, addressed by path.

    The workspace decides existence, so a file that is not there is a 404. A
    record that is not there is not an error — a file the bot created never had
    one.
    """
    safe = _require_path(path)
    # Same read-only policy the console's delete enforces
    # (``adapters/http/resources/file_router.py:381``) — dotfiles and the
    # workspace-root identity files. Addressing by path is what makes these
    # reachable at all: they never had a resource record, so the old
    # id-addressed delete could not name them and needed no guard.
    if is_readonly(safe):
        raise HTTPException(status_code=403, detail="Cannot delete read-only file")
    coords = _file_coords(bot_id, owner_id, bot_repo)
    entity_type, entity_id, engine_type = (
        coords.entity_type, coords.entity_id, coords.engine_type
    )
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
