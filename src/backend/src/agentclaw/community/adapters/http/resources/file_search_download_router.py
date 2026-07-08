"""File search and directory-download endpoints — split from file_router.py.

These two read-only operations were extracted to keep file_router.py under the
1000-line architecture cap.  They share the same models, helpers, and DI
dependencies as the main file_router; only the route handlers live here.
"""
from __future__ import annotations

import asyncio
import logging
import os
import stat
import tempfile
import zipfile
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.resources.dependencies.resource import (
    get_bot_workspace_dir,
    get_file_service,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import select_stage_bind_id
from agentclaw.community.api.baas_service import BaasServiceProtocol
from agentclaw.community.core.devices.services import device_info as device_info_lookup
from agentclaw.community.di import Injected
from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher
from agentclaw.community.core.config_compose.teclaw_paths import WORKSPACE_NS
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)

from agentclaw.community.adapters.http.resources.schemas import FileItem, FileListResponse
from agentclaw.community.adapters.http.resources.file_router import _resolve_params
# Browser filtering rules live in the service (their canonical owner after the
# file_router migration); this legacy search/zip router still consumes them until it
# is migrated in a later SDD step.
from agentclaw.community.core.services.resource_file_service import (
    _HIDDEN_BASENAMES,
    _HIDDEN_DIRNAMES,
    _SKILLS_LOCAL_RELPATH,
    is_readonly,
)

# Per-file cap for the Arca zip download (same Arca whole-file-into-memory concern as
# the single-file download guard, which now lives in ArcaDeviceFileSystem). Kept as a
# local copy until this legacy search/zip router is migrated.
_ARCA_DOWNLOAD_SIZE_LIMIT = 100 * 1024 * 1024  # 100 MB


# Container-internal workspace roots per engine — used to build the in-container
# ``absolute_path`` for search results (the file endpoints now return the logic view,
# but search still surfaces the container path until it is migrated).
_ARCA_CONTAINER_WORKSPACE_ROOTS: dict[str, str] = {
    "openclaw": "/home/admin/.openclaw/workspace",
    "aicoding": "/home/admin/.aicoding/workspace",
    "claude_code": "/home/admin/.claude_code/workspace",
}


def _abs_path(workspace_dir, rel_path: str, engine_type: str | None = None) -> str:
    """In-container ``absolute_path`` for a search/zip result (Arca uses the
    container workspace root; otherwise the host workspace dir)."""
    if engine_type and engine_type in _ARCA_CONTAINER_WORKSPACE_ROOTS:
        base = _ARCA_CONTAINER_WORKSPACE_ROOTS[engine_type].rstrip("/")
    else:
        base = str(workspace_dir).rstrip("/")
    return f"{base}/{rel_path}" if rel_path else base


def _reject_traversal(path: str) -> None:
    """Block directory traversal on endpoints whose Arca branch builds an
    absolute Bolt path by string-joining (no ``..`` rejection there)."""
    if path and ".." in path.split("/"):
        raise HTTPException(status_code=400, detail="Invalid path: directory traversal not allowed")


# ---- Caps for search and directory download ----
_SEARCH_MAX_RESULTS = 500
_DIRECTORY_DOWNLOAD_SIZE_LIMIT = 30 * 1024 * 1024  # 30 MB
_ZIP_DOWNLOAD_TOTAL_LIMIT = 500 * 1024 * 1024  # 500 MB
_ZIP_DOWNLOAD_MAX_FILES = 5000


def _passes_search_filter(rel_path: str) -> bool:
    """Whether a search/listing result (workspace-relative ``rel_path``) is
    browsable — mirrors the hidden rules ``list_files`` applies, but scoped to
    the whole tree so deep results don't leak system dirs.

    Hidden: any dotfile segment; the system top-level dirs in ``_HIDDEN_DIRNAMES``
    (``state``/``skills``/``conf``/``*_conf``) — except the injected
    ``skills/skills-local`` subtree; and root-level ``_HIDDEN_BASENAMES``.
    """
    segments = rel_path.split("/")
    if any(seg.startswith(".") for seg in segments):
        return False
    top = segments[0]
    if top in _HIDDEN_DIRNAMES:
        return (
            rel_path == _SKILLS_LOCAL_RELPATH
            or rel_path.startswith(_SKILLS_LOCAL_RELPATH + "/")
        )
    if len(segments) == 1 and top in _HIDDEN_BASENAMES:
        return False
    return True


def _search_should_descend(ws_dir_rel: str) -> bool:
    """Whether the search walk should descend into directory ``ws_dir_rel``
    (workspace-relative).  Mirrors :func:`_passes_search_filter`'s hidden rules
    but answers the *traversal* question so we never walk into big system
    subtrees (``state``/``skills``/``conf``/``*_conf``) — only the browsable
    ``skills/skills-local`` subtree survives.  Keeps search scoped to exactly
    what the resource browser shows, instead of walking the whole tree and
    filtering after (which timed out on large workspaces).
    """
    segments = ws_dir_rel.split("/")
    if any(seg.startswith(".") for seg in segments):
        return False
    if segments[0] not in _HIDDEN_DIRNAMES:
        return True
    # Hidden system dir: descend only along the skills/skills-local path so the
    # injected subtree stays reachable; everything else under it is pruned.
    return (
        ws_dir_rel == _SKILLS_LOCAL_RELPATH.split("/")[0]  # "skills" — on the way down
        or ws_dir_rel == _SKILLS_LOCAL_RELPATH
        or ws_dir_rel.startswith(_SKILLS_LOCAL_RELPATH + "/")
    )


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["resource-files"])


def _bind_id_from_publish(
    publish_id: str,
    publish_repo: BotPublishRepositoryProtocol,
) -> Optional[int]:
    """Resolve a publish record's stage binding id (the published device).

    ``DeviceContextResolver.resolve_for_binding`` then builds the device-fs for
    that exact binding — the published-stage device, not the bot's DRAFT binding
    (which is what ``resolve_for_bot`` would target). Returns ``None`` when the
    record / stage binding can't be resolved.
    """
    try:
        publish_record = publish_repo.get_by_id(int(publish_id))
        if not publish_record:
            logger.warning(f"[_bind_id_from_publish] publish_id={publish_id} not found")
            return None
        binding_info = (publish_record.ext or {}).get("binding", {})
        bind_id = select_stage_bind_id(binding_info, publish_record.status)
        if not bind_id:
            logger.warning(f"[_bind_id_from_publish] publish_id={publish_id} bind_id not found in ext")
            return None
        return bind_id
    except Exception as e:
        logger.error(f"[_bind_id_from_publish] Failed for publish_id={publish_id}: {e}")
        return None


def _resolve_walk_device_fs(
    *,
    publish_id: Optional[str],
    bot_id: str,
    owner_id: str,
    operator_id: str,
    engine_type: str,
    publish_repo: BotPublishRepositoryProtocol,
    bot_repo: BotRepository,
    resolver: DeviceContextResolver,
    dispatcher: DeviceFilesystemDispatcher,
    device_uuid: Optional[str] = None,
):
    """The device-fs to walk for search/zip, or ``None`` → local-FS fallback.

    ``publish_id`` → the published stage binding (``resolve_for_binding``); else the
    bot's draft device when it's a container-backed provider (arca / baas service).
    All providers address files uniformly via the ``workspace/<rel>`` namespace, so
    the caller just walks ``list_dir`` — no arca-specific path here.

    ``device_uuid`` (optional) locks a specific instance for multi-instance service
    bots; omitted → provider auto-selects an active instance.
    """
    if publish_id:
        bind_id = _bind_id_from_publish(publish_id, publish_repo)
        if bind_id is None:
            return None
        ctx = resolver.resolve_for_binding(
            bind_id, operator_id, bot_id=bot_id, device_uuid=device_uuid,
        )
        return dispatcher.dispatch_addressed(
            ctx, namespace=WORKSPACE_NS, entity_type="staff",
            entity_id=owner_id, bot_id=bot_id, engine_type=engine_type,
        )
    device_provider, _ = device_info_lookup.get_device_info(bot_id, owner_id, bot_repo)
    if device_provider not in ("arca", "baas"):
        return None
    return _device_fs_for_bot(
        bot_id, owner_id, engine_type, resolver, dispatcher, device_uuid=device_uuid,
    )


def _device_fs_for_bot(
    bot_id: str,
    owner_id: str,
    engine_type: str,
    resolver: DeviceContextResolver,
    dispatcher: DeviceFilesystemDispatcher,
    device_uuid: Optional[str] = None,
):
    """Build the addressed device-fs for a bot's draft device — the same path
    ``list_files`` takes (``resolve_for_bot`` → ``dispatch_addressed``).

    Used by search/zip for any container-backed draft device (arca + baas
    service bot): both address files uniformly via the ``workspace/<rel>``
    namespace, so the caller just walks ``list_dir`` / reads ``read_file`` — no
    provider branching here. (Desktop baas bots keep files local and are handled
    by the caller before this.)

    ``device_uuid`` (optional) locks a specific instance for multi-instance service
    bots; omitted → provider auto-selects an active instance.

    Raises whatever ``resolve_for_bot`` raises (e.g. ``DeviceNotBoundError``); the
    caller treats an exception as "no device-fs → fall through to local FS", the
    prior behavior.
    """
    ctx = resolver.resolve_for_bot(bot_id, owner_id, device_uuid=device_uuid)
    return dispatcher.dispatch_addressed(
        ctx, namespace=WORKSPACE_NS, entity_type="staff", entity_id=owner_id,
        bot_id=bot_id, engine_type=engine_type,
    )


async def _walk_device_fs(
    device_fs,
    base_rel: str,
    should_descend,
    max_entries: int = 20000,
) -> list[dict]:
    """Client-side recursive walk over a device-fs, mirroring ``_walk_arca``.

    Lists one level at a time (``recursive=False``) — the engine's ``recursive=true``
    is not relied upon (the Bolt/arca variant returns empty on some sandboxes; the
    baas engine is different, but staying level-by-level keeps all providers uniform
    and reuses the existing ``should_descend`` pruning). ``base_rel`` is
    workspace-relative (``""`` = root); each entry's ``rel`` is **workspace-relative**
    (so it carries the ``base_rel`` prefix — e.g. walking ``"data"`` yields
    ``"data/report.csv"``, ready for the search filter / browser rules directly).
    ``should_descend`` receives that workspace-relative ``rel``. Returns ``None`` when
    the root level fails to list (parity with ``_walk_arca``'s "root failed" sentinel).
    """
    out: list[dict] = []
    root_failed = False

    async def walk(current_rel: str) -> None:
        nonlocal root_failed
        logical = f"{WORKSPACE_NS}/{current_rel}" if current_rel else WORKSPACE_NS
        try:
            entries = await device_fs.list_dir(logical)
        except Exception as e:
            logger.warning("[_walk_device_fs] list_dir failed rel=%s: %s", current_rel, e)
            entries = None
        if entries is None:
            if not current_rel:
                root_failed = True
            return
        for e in entries:
            name = e.get("name", "")
            if not name or name.startswith("."):
                continue
            rel = f"{current_rel}/{name}" if current_rel else name
            is_dir = e.get("is_dir", False)
            out.append({
                "name": name,
                "rel": rel,
                "is_dir": is_dir,
                "size": e.get("size"),
                "size_human": e.get("size_human"),
                "modified_at": e.get("modified_at"),
            })
            if len(out) >= max_entries:
                return
            if is_dir and (should_descend is None or should_descend(rel)):
                await walk(rel)
                if len(out) >= max_entries:
                    return

    await walk(base_rel)
    return None if root_failed else out


# ---------------------------------------------------------------------------
# GET /search — recursive filename search
# ---------------------------------------------------------------------------


@router.get("/search", response_model=FileListResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 只读操作，不记录日志
))
async def search_files(
    keyword: str = Query(..., min_length=1, max_length=200, description="Filename substring (case-insensitive)"),
    path: str = Query("", description="Subtree root to scope the search (default: workspace root)"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine override; defaults to bot's active_engine"),
    owner_id: Optional[str] = Query(None, description="Bot owner ID. Required for collaborators."),
    publish_id: Optional[str] = Query(None, description="Publish ID for reading from published bot device"),
    device_uuid: Optional[str] = Query(None, description="Device UUID for multi-instance targeting; omitted → active instance"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    publish_repo: BotPublishRepositoryProtocol = Injected(BotPublishRepositoryProtocol),
    baas_service: BaasServiceProtocol = Injected(BaasServiceProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(DeviceFilesystemDispatcher),
) -> FileListResponse:
    """Recursively search files/dirs by filename under the workspace (flat results)."""
    _reject_traversal(path)
    eid, ebid, eeng = _resolve_params(ctx, bot_id, engine_type, owner_id, bot_repo=bot_repo)
    workspace_dir = get_bot_workspace_dir(path_factory, eid, ebid, eeng, "staff")

    # desktop baas bot keeps files local — no container search.
    if not publish_id:
        dp, _ = device_info_lookup.get_device_info(ebid, eid, bot_repo)
        if dp == 'baas':
            bot_info = bot_repo.get_by_id_and_owner(ebid, eid)
            if bot_info and bot_info.get("bot_type") == "desktop":
                logger.info("[file_router.search_files] desktop bot — search not supported, returning empty")
                return FileListResponse(path=path, items=[])

    # arca + baas-service + published-stage devices all walk uniformly via the
    # device-fs (workspace/<rel> namespace); local/unbound → local-FS fallback.
    try:
        device_fs = _resolve_walk_device_fs(
            publish_id=publish_id, bot_id=ebid, owner_id=eid, operator_id=ctx.user_id,
            engine_type=eeng, publish_repo=publish_repo, bot_repo=bot_repo,
            resolver=resolver, dispatcher=device_fs_dispatcher, device_uuid=device_uuid,
        )
    except Exception as e:
        logger.warning("[file_router.search_files] device-fs resolve failed bot=%s: %s", ebid, e)
        device_fs = None
    if publish_id and device_fs is None:
        raise HTTPException(status_code=400, detail=f"Failed to get device info for publish_id={publish_id}")

    if device_fs is not None:
        entries = await _walk_device_fs(
            device_fs, path,
            should_descend=_search_should_descend,
            max_entries=_SEARCH_MAX_RESULTS,
        )
        if entries is None:
            return FileListResponse(path=path, items=[])
        items = []
        kw = keyword.lower()
        for e in entries:
            if kw not in e["name"].lower():
                continue
            rel = e["rel"]  # already workspace-relative
            if not _passes_search_filter(rel):
                continue
            is_dir = e.get("is_dir", False)
            items.append(FileItem(
                name=e["name"],
                path=rel,
                absolute_path=_abs_path(workspace_dir, rel, engine_type=eeng),
                is_dir=is_dir,
                readonly=is_readonly(rel),
                size=e.get("size") if not is_dir else None,
                size_human=e.get("size_human") if not is_dir else None,
                modified_at=e.get("modified_at"),
            ))
            if len(items) >= _SEARCH_MAX_RESULTS:
                break
        return FileListResponse(path=path, items=items[:_SEARCH_MAX_RESULTS])

    # Local filesystem
    file_service = get_file_service(workspace_dir)
    matches = await file_service.search_flat(keyword, base=path, max_results=_SEARCH_MAX_RESULTS)
    items = []
    for m in matches:
        if not _passes_search_filter(m["path"]):
            continue
        is_dir = m["is_dir"]
        items.append(FileItem(
            name=m["name"],
            path=m["path"],
            absolute_path=_abs_path(workspace_dir, m["path"]),
            is_dir=is_dir,
            readonly=is_readonly(m["path"]),
            size=m.get("size") if not is_dir else None,
            size_human=m.get("size_human") if not is_dir else None,
            modified_at=m.get("modified_at"),
        ))
    return FileListResponse(path=path, items=items[:_SEARCH_MAX_RESULTS])

# ---------------------------------------------------------------------------
# GET /download-dir — directory zip download
# ---------------------------------------------------------------------------


def _build_zip_local(files: list[dict], top_dir: str, zip_path: str) -> None:
    """Write local-FS files into a zip on disk (runs in a worker thread).

    ``files`` are ``{"rel", "abs", "size"}`` from ``FileService.list_tree_files``;
    ``zf.write`` streams each file from disk so peak memory stays small.
    """
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for entry in files:
            arcname = f"{top_dir}/{entry['rel']}" if top_dir else entry["rel"]
            zf.write(entry["abs"], arcname)


def _download_logical(rel: str) -> str:
    """Logical read path for a device-fs walk entry.

    ``rel`` is already workspace-relative and carries the requested ``path``
    prefix (see :func:`_walk_device_fs`, which walks from ``base_rel=path``),
    so the read path is a *flat* join onto the workspace namespace. Re-stitching
    ``path`` here would double it (``workspace/memory/memory/foo.txt``); the
    device would miss every file and the zip would come back empty.
    """
    return f"{WORKSPACE_NS}/{rel}"


def _download_arcname(rel: str, path: str) -> str:
    """Zip entry name for a device-fs walk entry, relative to the requested folder.

    Strips the ``path`` prefix ``rel`` carries so the zip stays flat
    (``memory/foo.txt``, not ``memory/memory/foo.txt``); the caller prepends
    ``folder_name``.
    """
    return rel[len(path) + 1:] if path else rel


def _zip_file_entry(name: str) -> zipfile.ZipInfo:
    """A ``ZipInfo`` for a downloaded FILE with archive-tool-compatible metadata.

    ``zipfile.ZipFile.writestr(str, bytes)`` leaves ``external_attr``'s Unix
    type bits at ``0`` (no ``S_IFREG``) and emits no directory entry, so the
    zip carries no machine-readable "this is a regular file / that is a
    directory" tag. macOS Archive Utility reads those type bits and, finding
    none, refuses the entry with "archive is empty / no readable items" — even
    though ``unzip``/``ditto`` (and Windows Explorer, which keys off the
    trailing ``/`` instead) extract fine. Setting ``S_IFREG`` (+ ``0o644``) and
    a trailing-``/`` directory ``ZipInfo`` from the caller brings the zip in
    line with what the platform ``zip`` command emits.
    """
    zi = zipfile.ZipInfo(name)
    zi.external_attr = (stat.S_IFREG | 0o644) << 16
    return zi


def _zip_dir_entry(name: str) -> zipfile.ZipInfo:
    """A trailing-``/`` directory ``ZipInfo`` so GUI archive tools see the parent
    folders. ``writestr`` with ``bytes`` would otherwise omit directory entries."""
    zi = zipfile.ZipInfo(name if name.endswith("/") else name + "/")
    zi.external_attr = (stat.S_IFDIR | 0o755) << 16
    return zi


@router.get("/download-dir")
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 只读操作，不记录日志
))
async def download_directory(
    path: str = Query(..., min_length=1, description="Directory relative path from workspace root"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine override; defaults to bot's active_engine"),
    owner_id: Optional[str] = Query(None, description="Bot owner ID. Required for collaborators."),
    publish_id: Optional[str] = Query(None, description="Publish ID for reading from published bot device"),
    device_uuid: Optional[str] = Query(None, description="Device UUID for multi-instance targeting; omitted → active instance"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    publish_repo: BotPublishRepositoryProtocol = Injected(BotPublishRepositoryProtocol),
    baas_service: BaasServiceProtocol = Injected(BaasServiceProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(DeviceFilesystemDispatcher),
):
    """Download a directory as a streamed zip. Supports local FS + Arca + BaaS only."""
    _reject_traversal(path)
    eid, ebid, eeng = _resolve_params(ctx, bot_id, engine_type, owner_id, bot_repo=bot_repo)
    workspace_dir = get_bot_workspace_dir(path_factory, eid, ebid, eeng, "staff")

    # desktop baas bot keeps files local — no container download.
    if not publish_id:
        dp, _ = device_info_lookup.get_device_info(ebid, eid, bot_repo)
        if dp == 'baas':
            bot_info = bot_repo.get_by_id_and_owner(ebid, eid)
            if bot_info and bot_info.get("bot_type") == "desktop":
                raise HTTPException(status_code=400, detail="桌面 bot 文件已在本地，无需打包下载")
    try:
        device_fs = _resolve_walk_device_fs(
            publish_id=publish_id, bot_id=ebid, owner_id=eid, operator_id=ctx.user_id,
            engine_type=eeng, publish_repo=publish_repo, bot_repo=bot_repo,
            resolver=resolver, dispatcher=device_fs_dispatcher, device_uuid=device_uuid,
        )
    except Exception as e:
        logger.warning("[download_directory] device-fs resolve failed bot=%s: %s", ebid, e)
        if publish_id:
            raise HTTPException(status_code=400, detail=f"Failed to get device info for publish_id={publish_id}")
        raise HTTPException(status_code=400, detail=f"Failed to resolve device for bot {ebid}: {e}")
    if publish_id and device_fs is None:
        raise HTTPException(status_code=400, detail=f"Failed to get device info for publish_id={publish_id}")

    folder_name = path.rsplit("/", 1)[-1] or "workspace"
    zip_name = f"{folder_name}.zip"
    content_disp = f"attachment; filename*=UTF-8''{quote(zip_name)}"

    def _enforce_caps(total: int, count: int) -> None:
        if count == 0:
            raise HTTPException(status_code=404, detail="Directory is empty or does not exist")
        if total > _DIRECTORY_DOWNLOAD_SIZE_LIMIT:
            raise HTTPException(
                status_code=413,
                detail=f"文件夹总大小超过30MB限制（当前约{total // 1024 // 1024}MB），请缩小下载范围",
            )
        if count > _ZIP_DOWNLOAD_MAX_FILES:
            raise HTTPException(status_code=413, detail=f"Too many files to download ({count}, max {_ZIP_DOWNLOAD_MAX_FILES})")
        if total > _ZIP_DOWNLOAD_TOTAL_LIMIT:
            raise HTTPException(status_code=413, detail=f"Folder too large to download ({total} bytes, max {_ZIP_DOWNLOAD_TOTAL_LIMIT} bytes)")

    # arca + baas-service + published-stage devices all walk + read uniformly via
    # the device-fs (workspace/<rel> namespace); local/unbound → local-FS below.
    if device_fs is not None:
        entries = await _walk_device_fs(
            device_fs, path, should_descend=None, max_entries=_ZIP_DOWNLOAD_MAX_FILES + 1,
        )
        if entries is None:
            raise HTTPException(status_code=404, detail="Directory not found on device")
        files = [e for e in entries if not e.get("is_dir")]
        total = sum((f.get("size") or 0) for f in files)
        _enforce_caps(total, len(files))
        for f in files:
            if (f.get("size") or 0) > _ARCA_DOWNLOAD_SIZE_LIMIT:
                raise HTTPException(status_code=413, detail=f"File too large for download: {f['rel']} ({f.get('size')} bytes, max {_ARCA_DOWNLOAD_SIZE_LIMIT})")

        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp.close()
        try:
            with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                # Emit the root folder entry so macOS/Windows GUI archive tools
                # see the requested folder up front (mirrors the platform zip).
                zf.writestr(_zip_dir_entry(folder_name), b"")
                for f in files:
                    logical = _download_logical(f["rel"])
                    try:
                        data = await device_fs.read_file(logical)
                    except Exception as e:
                        logger.warning("[download_directory] device-fs read failed, skipping %s: %s", f["rel"], e)
                        continue
                    if data is None:
                        continue
                    zf.writestr(
                        _zip_file_entry(f"{folder_name}/{_download_arcname(f['rel'], path)}"),
                        data,
                    )
        except Exception:
            os.unlink(tmp.name)
            raise
        return FileResponse(
            tmp.name, media_type="application/zip",
            headers={"Content-Disposition": content_disp},
            background=BackgroundTask(os.unlink, tmp.name),
        )

    # Local filesystem
    file_service = get_file_service(workspace_dir)
    files, total = await file_service.list_tree_files(base=path)
    _enforce_caps(total, len(files))

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    try:
        await asyncio.to_thread(_build_zip_local, files, folder_name, tmp.name)
    except Exception:
        os.unlink(tmp.name)
        raise
    return FileResponse(
        tmp.name, media_type="application/zip",
        headers={"Content-Disposition": content_disp},
        background=BackgroundTask(os.unlink, tmp.name),
    )
