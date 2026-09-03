"""ResourceFileService — provider-agnostic resource-file operations.

Addresses every file by the logical ``workspace/<rel>`` namespace and delegates byte
movement to the ``DeviceFileSystem`` the dispatcher mints for the bot. The
per-provider composition of ``workspace/<rel>`` into the real container/host address
lives in the plugin's injected ``path_mapper`` (built by
:meth:`DeviceFilesystemDispatcher.dispatch_addressed`), so this service carries no
provider/path branching — mirroring :class:`IdentityService`.

What the service owns (moved out of ``adapters/http/resources/file_router.py``):

- resolve the device context (draft via ``resolve_for_bot``; publish-stage via
  ``resolve_for_binding`` — added in a follow-up task) and dispatch by the
  ``workspace`` namespace;
- the file-browser rules: dotfile / hidden-dir / hidden-file filtering, read-only
  flagging, the ``skills-local`` root injection;
- the ``absolute_path`` field: the device's own absolute path for the entry — the
  engine returns it per listing entry (``path``: container path for arca/openclaw/
  aicoding/claude_code, host path for local). teclaw doesn't expose an absolute path
  yet, so it returns its logical (root-relative) path there for now; once teclaw
  matches the other engines this works automatically and the fallback can be dropped.

Each engine entry also carries ``relative_path`` (relative to the *listed* dir), used
to build the workspace-relative ``path`` so a future recursive listing stays correct;
teclaw lacks it, so we fall back to ``<request_path>/<name>`` (valid non-recursively).

The HTTP adapter maps the returned plain dicts / bytes to its response schemas.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from injector import inject

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_config_surface.coords import BotConfigCoords
from agentclaw.community.core.bot_management.services.engine_resolver import (
    resolve_runtime_engine_for_bot,
)
from agentclaw.community.core.config_compose.teclaw_paths import WORKSPACE_NS
from agentclaw.community.core.resources.service import InvalidResourcePathError
from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError,
    DeviceContext,
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.devices.services.device_filesystem import (
    FileTooLargeError as DeviceFileTooLargeError,
)
from agentclaw.community.core.resources.service import (
    DirectoryTooLargeError,
    ResourceNotFoundError,
)
from agentclaw.community.core.resources.services.file_service import (
    FileNode,
    admission_refusal,
)
from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
from agentclaw.community.core.workspace.constants import SUPPORTED_ENGINE_TYPES
from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher
from agentclaw.community.log import get_logger

logger = get_logger()


# ── File-browser rules (shared with the sibling search/zip router) ────────────

# "skills" is hidden, but "skills/skills-local" is injected into root listings so
# users can browse/edit their local skill files (arca/local layout). teclaw keeps
# local skills flat at /workspace/skills-local, so it needs no injection.
_SKILLS_LOCAL_RELPATH = "skills/skills-local"
_POOL_SKILLS_LOCAL_RELPATH = "skills-pool/skills-local"

# System files hidden from the resource browser (OpenClaw identity/config .md files).
_HIDDEN_BASENAMES = {
    "AGENTS.md", "RULES.md", "OKR.md", "SAFETY.md", "SOUL.md",
    "OUTPUT.md", "MEMORY.md", "IDENTITY.md", "USER.md", "TOOLS.md",
    "HEARTBEAT.md", "BOOTSTRAP.md",
}

# Hidden directories (system state, skill symlinks, per-engine config dirs).
_HIDDEN_DIRNAMES = {
    "state",
    "skills",
    "skills-pool",
    "conf",
    *(f"{engine}_conf" for engine in SUPPORTED_ENGINE_TYPES),
}


def is_readonly(path: str) -> bool:
    """Whether a file/dir is read-only (cannot delete): dotfiles, or an OpenClaw
    identity ``.md`` file in the workspace root."""
    basename = path.rsplit("/", 1)[-1] if "/" in path else path
    if basename.startswith("."):
        return True
    if "/" not in path and basename in _HIDDEN_BASENAMES:
        return True
    return False


# ── Directory-download caps (the openapi download-dir endpoint) ──────────────
#: Enforced from the listing's sizes *before* a single byte is read, then
#: re-counted against the bytes actually streamed — a stale or lying listing
#: cannot widen them. The per-file number matches the guard
#: ``ArcaDeviceFileSystem`` enforces under ``enforce_download_limit=True``.
DIRECTORY_DOWNLOAD_MAX_FILES = 5000
DIRECTORY_DOWNLOAD_MAX_TOTAL_BYTES = 500 * 1024 * 1024
DIRECTORY_DOWNLOAD_MAX_FILE_BYTES = 100 * 1024 * 1024


def safe_workspace_path(path: str) -> str:
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

    Lives here rather than in the router that used to own it (as ``_safe_path``)
    because it is a statement about the workspace, not about HTTP — and because
    manifest apply enforces the same rule without a request to hang it on.
    """
    segments = [s for s in path.split("/") if s and s != "."]
    if any(s == ".." for s in segments):
        raise InvalidResourcePathError(f"path escapes the workspace: {path!r}")
    return "/".join(segments)


def require_workspace_path(path: str) -> str:
    """``safe_workspace_path``, refusing the empty result.

    Every endpoint but the listing addresses one entry, and the workspace root
    is not one: there is nothing to download, delete or stat about it.
    """
    safe = safe_workspace_path(path)
    if not safe:
        raise InvalidResourcePathError("path is required")
    return safe


def is_write_forbidden(safe: str) -> bool:
    """Whether the read-only policy protects this path against creation.

    Applied to **creation** as well as deletion, so that the surface cannot be
    talked into making something it then refuses to manage: a listing hides
    dotfiles and the root identity files, and delete refuses them, so an upload
    or mkdir that accepted one would leave an entry this API can neither show
    nor remove. Uploading a workspace-root identity file would also overwrite
    the bot's own configuration through a resource endpoint, which is not what
    that surface is for.

    Every ancestor is checked, not just the whole path. :func:`is_readonly` looks
    at the final segment only, so ``.private/file.md`` passes it — the leaf is an
    ordinary name — while creating it brings a hidden ``.private`` directory into
    existence along the way. Removing the visible descendant afterwards would
    then leave a directory this API created and can neither list nor delete.

    Returns a verdict and raises nothing, deliberately. The refusal the public
    API answers is an ``HTTPException`` with a specific body, and mapping a
    domain error back onto that body byte-for-byte is harder to be sure of than
    leaving the ``raise`` where it already is. Core decides; the adapter phrases.
    """
    segments = safe.split("/")
    return any(
        is_readonly("/".join(segments[: depth + 1]))
        for depth in range(len(segments))
    )


def resource_coords_from_record(
    bot_id: str, owner_id: str, bot_repo: BotRepository
) -> BotConfigCoords:
    """Where the ``resources`` category writes, for a bot that exists.

    ``ResourceFileService`` addresses a bot's workspace by these three
    coordinates, and ``DeviceContext`` carries none of them — it holds
    provider / conn_info / binding only. Mirrors the console router's
    ``_resolve_params`` (``adapters/http/resources/file_router.py:71``): the
    entity is the bot owner, and ``engine_type`` defaults to the bot's
    ``active_engine``. ``entity_type`` is ``"staff"``, matching
    ``ResourceFileService``'s own default.

    **This performs no ownership guard**, which is a preserved fact rather than
    an oversight of the move: the router's ``_file_coords`` performed none
    either, and adding one here would change who the resources endpoints admit.
    That it differs from ``engine_config``'s equivalent — which does guard — is
    exactly what putting the five side by side was meant to make visible.
    """
    engine_type = resolve_runtime_engine_for_bot(
        bot_id=bot_id, owner_id=owner_id, override=None, bot_repo=bot_repo
    )
    return BotConfigCoords(
        bot_id=bot_id,
        owner_id=owner_id,
        entity_type="staff",
        entity_id=owner_id,
        engine_type=engine_type,
    )


def resource_coords_from_spec(
    bot_id: str, owner_id: str, engine_type: str
) -> BotConfigCoords:
    """The same address, for a bot that does not exist yet.

    The engine comes from the create request instead of the bot's
    ``active_engine``, because in the first phase of the create flow there is no
    record to read one off. No repository is touched and no ownership is
    checked: there is nothing yet to own, and whether the caller may create a
    bot at all is what ``check_create_bot_preflight`` already decides
    (``core/bot_management/create_flow.py:494``), beside which the manifest is
    validated.

    No caller until W13 (#1696).
    """
    return BotConfigCoords(
        bot_id=bot_id,
        owner_id=owner_id,
        entity_type="staff",
        entity_id=owner_id,
        engine_type=engine_type,
    )


class ResourceFileService:
    """Provider-agnostic resource-file operations over the ``workspace`` namespace."""

    @inject
    def __init__(
        self,
        publish_repo: BotPublishRepositoryProtocol,
        bot_repo: BotRepository,
        resolver: DeviceContextResolver,
        device_fs_dispatcher: DeviceFilesystemDispatcher,
    ):
        self._publish_repo = publish_repo
        self._bot_repo = bot_repo
        self._resolver = resolver
        self._device_fs_dispatcher = device_fs_dispatcher

    # ── internal: context + addressing ───────────────────────────────────────

    def _resolve_ctx(
        self, *, bot_id: str, entity_id: str, publish_id: str | None = None,
        device_uuid: str | None = None,
    ) -> DeviceContext:
        """Resolve the device context.

        Draft → ``resolve_for_bot``. Publish-stage (``publish_id``) → resolve the
        stage ``bind_id`` (``ext.binding`` online→verify) and ``resolve_for_binding``
        — covering arca / baas / teclaw uniformly (replaces the old arca sandbox-id
        bypass + teclaw special case). A missing/unresolvable stage binding raises
        ``ValueError`` (mapped to HTTP 400 by the adapter), matching today.

        ``device_uuid`` (optional) locks a specific instance for multi-instance
        service bots; omitted → provider auto-selects an active instance.
        """
        if publish_id:
            bind_id = self._stage_bind_id(publish_id)
            if not bind_id:
                raise ValueError(
                    f"Failed to get device info for publish_id={publish_id}"
                )
            try:
                return self._resolver.resolve_for_binding(
                    int(bind_id), entity_id, bot_id=bot_id, device_uuid=device_uuid,
                )
            except (
                ValueError,  # non-numeric bind_id — was shielded by the old broad except
                DeviceNotBoundError,
                UnknownProviderError,
                ConnInfoBuildError,
            ) as e:
                logger.warning(
                    "[ResourceFileService] publish_id=%s bind_id=%s resolve failed: %s",
                    publish_id, bind_id, e,
                )
                raise ValueError(
                    f"Failed to get device info for publish_id={publish_id}"
                ) from e
        return self._resolver.resolve_for_bot(bot_id, entity_id, device_uuid=device_uuid)

    def _stage_bind_id(self, publish_id: str) -> int | None:
        """Stage ``bind_id`` from the publish record, selected by ``record.status``.

        ``bind_id`` may be 0; the caller's ``if not bind_id`` treats a missing/0
        bind_id as "no stage binding" (binding PKs are ≥1, so 0 is never a real
        binding).
        """
        from agentclaw.community.core.service_bot.repository.models import select_stage_bind_id

        try:
            record = self._publish_repo.get_by_id(int(publish_id))
        except (TypeError, ValueError):
            return None
        if not record:
            return None
        binding = (record.ext or {}).get("binding", {})
        return select_stage_bind_id(binding, record.status)

    def _device_fs(
        self,
        ctx: DeviceContext,
        *,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        engine_type: str,
    ):
        return self._device_fs_dispatcher.dispatch_addressed(
            ctx, namespace=WORKSPACE_NS, entity_type=entity_type,
            entity_id=entity_id, bot_id=bot_id, engine_type=engine_type,
        )

    @staticmethod
    def _logical(path: str) -> str:
        """``rel`` → ``workspace/<rel>`` (or bare ``workspace`` for the root)."""
        return f"{WORKSPACE_NS}/{path}" if path else WORKSPACE_NS

    @staticmethod
    def _rel_path(request_path: str, entry: dict[str, Any]) -> str:
        """Workspace-relative path for a listing entry.

        Engines return ``relative_path`` (relative to the *listed* dir), which keeps
        a recursive listing correct; teclaw doesn't, so fall back to the entry name
        (valid for the non-recursive listings we do today).
        """
        rel_in_listing = entry.get("relative_path")
        leaf = rel_in_listing if rel_in_listing else entry.get("name", "")
        return f"{request_path}/{leaf}" if request_path else leaf

    # ── operations ───────────────────────────────────────────────────────────

    async def list_dir(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
        publish_id: str | None = None,
        device_uuid: str | None = None,
    ) -> list[dict[str, Any]]:
        """List a directory under the workspace, provider-blind.

        Returns plain dicts shaped for the ``FileItem`` schema. Applies the
        dotfile / hidden-dir / hidden-file filtering, read-only flagging, and the
        ``skills-local`` root injection (non-teclaw).

        ``device_uuid`` (optional) locks a specific instance for multi-instance
        service bots; omitted → provider auto-selects an active instance.
        """
        ctx = self._resolve_ctx(
            bot_id=bot_id, entity_id=entity_id, publish_id=publish_id,
            device_uuid=device_uuid,
        )
        device_fs = self._device_fs(
            ctx, entity_type=entity_type, entity_id=entity_id,
            bot_id=bot_id, engine_type=engine_type,
        )

        entries = await device_fs.list_dir(self._logical(path))
        if entries is None:
            return []

        items: list[dict[str, Any]] = []
        for e in entries:
            name = e.get("name", "")
            if name.startswith("."):
                continue
            is_dir = e.get("is_dir", False)
            if is_dir and not path and name in _HIDDEN_DIRNAMES:
                continue
            if not path and name in _HIDDEN_BASENAMES:
                continue
            rel = self._rel_path(path, e)
            items.append({
                "name": name,
                "path": rel,
                # the device's absolute path (engine ``path``); logic-view fallback
                # only when the engine doesn't expose one (legacy teclaw).
                "absolute_path": e.get("path") or self._logical(rel),
                "is_dir": is_dir,
                "readonly": is_readonly(rel),
                "size": e.get("size") if not is_dir else None,
                "size_human": e.get("size_human") if not is_dir else None,
                "modified_at": e.get("modified_at"),
            })

        # skills-local injection (arca/local nest it under the hidden "skills" dir,
        # so it must be injected; teclaw keeps it flat at /workspace/skills-local and
        # lists naturally; baas/desktop never injected — don't probe it for them).
        if not path and ctx.provider in ("arca", "local", "baas"):
            for local_relpath in (
                _SKILLS_LOCAL_RELPATH,
                _POOL_SKILLS_LOCAL_RELPATH,
            ):
                parent = local_relpath.rsplit("/", 1)[0]
                try:
                    skills_entries = await device_fs.list_dir(
                        f"{WORKSPACE_NS}/{parent}"
                    )
                except Exception as e:
                    # Either layout may be absent. Keep probing the other one
                    # and never fail the root listing for this optional entry.
                    logger.warning(
                        "[list_dir] skills-local probe skipped path=%s: %s",
                        parent,
                        e,
                    )
                    continue
                skill_local = next(
                    (
                        entry
                        for entry in skills_entries or []
                        if entry.get("name") == "skills-local"
                        and entry.get("is_dir", False)
                    ),
                    None,
                )
                if skill_local is not None:
                    items.append({
                        "name": "skills-local",
                        "path": local_relpath,
                        # the probed entry's own absolute path (logic-view fallback).
                        "absolute_path": skill_local.get("path")
                        or self._logical(local_relpath),
                        "is_dir": True,
                        "readonly": False,
                        "size": None,
                        "size_human": None,
                        "modified_at": None,
                    })
                    break

        return items

    async def read_file(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
        publish_id: str | None = None,
        device_uuid: str | None = None,
        enforce_download_limit: bool = False,
    ) -> bytes | None:
        """Read a file's bytes via the device, provider-blind.

        ``enforce_download_limit`` is forwarded to the plugin: an impl that loads the
        whole file into memory (Arca) rejects an oversized download with
        :class:`FileTooLargeError` before reading; others ignore it.

        ``device_uuid`` (optional) locks a specific instance for multi-instance
        service bots; omitted → provider auto-selects an active instance.
        """
        ctx = self._resolve_ctx(
            bot_id=bot_id, entity_id=entity_id, publish_id=publish_id,
            device_uuid=device_uuid,
        )
        device_fs = self._device_fs(
            ctx, entity_type=entity_type, entity_id=entity_id,
            bot_id=bot_id, engine_type=engine_type,
        )
        return await device_fs.read_file(
            self._logical(path), enforce_download_limit=enforce_download_limit
        )

    async def iter_directory_files(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> AsyncIterator[tuple[str, bytes]]:
        """Walk a directory, yielding ``(name, content)`` for every regular file.

        ``name`` is relative to the *requested* directory — downloading
        ``docs`` yields ``a.txt``, not ``docs/a.txt`` — so the caller prefixes
        it with whatever archive root it likes. One device-context resolution
        serves the whole walk (unlike ``read_file`` per entry); listing is
        level-by-level because no engine's ``recursive`` flag is trusted
        anywhere in the repo.

        Filtering mirrors ``list_dir``: dotfiles skipped at every level, the
        hidden system names only at the workspace root — a root download
        contains what the file browser shows, no more. The ``skills-local``
        injection is *not* reproduced: it is a listing-level synthetic, and a
        download must contain what the device actually holds.

        Caps are enforced twice — from the listing's sizes before any byte is
        read, then against the bytes actually streamed — and both raise
        ``DirectoryTooLargeError``; a stale or lying listing cannot widen them.
        A missing root raises ``ResourceNotFoundError``; an *empty* directory
        yields nothing and is not an error — the two answers are different and
        this walk can tell them apart.

        Race rule: an entry that *vanishes* mid-walk (a listing or a read
        answering ``None``) is skipped — a live workspace is allowed to change
        under the walk. An entry that *errors* aborts the whole download: the
        alternative is a 200 archive silently missing files, which a public
        API may not serve.
        """
        ctx = self._resolve_ctx(bot_id=bot_id, entity_id=entity_id)
        device_fs = self._device_fs(
            ctx, entity_type=entity_type, entity_id=entity_id,
            bot_id=bot_id, engine_type=engine_type,
        )

        root = await device_fs.list_dir(self._logical(path))
        if root is None:
            raise ResourceNotFoundError(f"no such directory: {path!r}")

        count = 0
        listed_total = 0
        streamed_total = 0
        queue: list[tuple[str, list[dict[str, Any]]]] = [(path, root)]
        while queue:
            current, entries = queue.pop(0)
            for entry in sorted(entries, key=lambda e: e.get("name", "")):
                name = entry.get("name", "")
                if name.startswith("."):
                    continue
                is_dir = bool(entry.get("is_dir", False))
                # The hidden system names are a *workspace-root* rule in
                # ``list_dir`` — the first level of a root walk is exactly
                # that listing; deeper levels and sub-directory walks keep
                # everything non-dot.
                if not current and not path:
                    if is_dir and name in _HIDDEN_DIRNAMES:
                        continue
                    if name in _HIDDEN_BASENAMES:
                        continue
                rel = self._rel_path(current, entry)
                if is_dir:
                    sub = await device_fs.list_dir(self._logical(rel))
                    if sub is None:
                        continue  # vanished mid-walk — the race rule
                    queue.append((rel, sub))
                    continue

                count += 1
                listed_size = entry.get("size") or 0
                listed_total += listed_size
                if (
                    count > DIRECTORY_DOWNLOAD_MAX_FILES
                    or listed_size > DIRECTORY_DOWNLOAD_MAX_FILE_BYTES
                    or listed_total > DIRECTORY_DOWNLOAD_MAX_TOTAL_BYTES
                ):
                    raise DirectoryTooLargeError(
                        f"listing exceeds the directory-download caps at {rel!r}"
                    )
                try:
                    content = await device_fs.read_file(
                        self._logical(rel), enforce_download_limit=True
                    )
                except DeviceFileTooLargeError as exc:
                    raise DirectoryTooLargeError(
                        f"file exceeds the per-file cap: {rel!r}"
                    ) from exc
                if content is None:
                    continue  # vanished mid-walk — the race rule
                streamed_total += len(content)
                if (
                    len(content) > DIRECTORY_DOWNLOAD_MAX_FILE_BYTES
                    or streamed_total > DIRECTORY_DOWNLOAD_MAX_TOTAL_BYTES
                ):
                    raise DirectoryTooLargeError(
                        f"streamed bytes exceed the directory-download caps at {rel!r}"
                    )
                # Relative to the requested directory, so the archive root is
                # the caller's choice (console: ``_download_arcname``).
                yield (rel[len(path) + 1:] if path else rel), content

    async def exists(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
        publish_id: str | None = None,
        device_uuid: str | None = None,
    ) -> bool:
        """Whether ``path`` is present in the bot's workspace, provider-blind.

        Same addressing as every other method here — the caller passes a
        workspace-relative path and never learns the container's layout.
        """
        ctx = self._resolve_ctx(
            bot_id=bot_id, entity_id=entity_id, publish_id=publish_id,
            device_uuid=device_uuid,
        )
        device_fs = self._device_fs(
            ctx, entity_type=entity_type, entity_id=entity_id,
            bot_id=bot_id, engine_type=engine_type,
        )
        return await device_fs.exists(self._logical(path))

    async def create_directory(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> None:
        """Create a directory by writing a ``.keep`` placeholder (mirrors the arca/
        teclaw strategy — the engine APIs auto-create parents on upload)."""
        ctx = self._resolve_ctx(bot_id=bot_id, entity_id=entity_id)
        device_fs = self._device_fs(
            ctx, entity_type=entity_type, entity_id=entity_id,
            bot_id=bot_id, engine_type=engine_type,
        )
        keep = f"{self._logical(path)}/.keep" if path else f"{WORKSPACE_NS}/.keep"
        await device_fs.write_file(keep, b"")

    async def delete(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> bool:
        """Delete a file or directory; returns False when nothing was deleted.

        A directory needs ``delete_tree``: the engines route the two through
        separate operations (``/api/file/remove`` vs ``/api/file/rmtree``), and
        the single-file one does not recurse — so sending every path to
        ``delete_file`` made the documented "or directory" half of this method
        silently unable to delete one.
        """
        ctx = self._resolve_ctx(bot_id=bot_id, entity_id=entity_id)
        device_fs = self._device_fs(
            ctx, entity_type=entity_type, entity_id=entity_id,
            bot_id=bot_id, engine_type=engine_type,
        )
        logical = self._logical(path)
        if await self._is_dir(device_fs, path):
            return await device_fs.delete_tree(logical)
        return await device_fs.delete_file(logical)

    async def _is_dir(self, device_fs: Any, path: str) -> bool:
        """Whether *path* names a directory, asked of the parent's listing.

        The parent rather than the path itself: listing a *file* is not a
        defined operation on the engines — ``list_dir`` raises rather than
        answering "not a directory" — so probing directly would turn every file
        delete into an error.

        Uses the raw device listing, not this class's filtered ``list_dir``, so
        the hidden system names stay deletable exactly as before. A listing
        failure answers "not a directory": the delete then takes the file
        branch, which is what it did unconditionally until now.
        """
        parent, _, leaf = path.rpartition("/")
        try:
            entries = await device_fs.list_dir(self._logical(parent))
        except Exception as exc:
            logger.warning(
                "[%s._is_dir] listing %r failed, assuming file: %s",
                type(self).__name__, parent, exc,
            )
            return False
        for entry in entries or ():
            if entry.get("name") == leaf:
                return bool(entry.get("is_dir", False))
        return False

    async def upload_file(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        target_dir: str,
        filename: str,
        data: bytes,
        preserve_structure: bool = False,
    ) -> dict[str, Any]:
        """Validate and write a single uploaded file under the workspace.

        Extension allow-list + size cap applied uniformly (the same rules the local/
        arca path enforced via ``FileService``). ``preserve_structure`` keeps the
        relative path from a directory upload (filename contains ``/``).
        """
        if not filename:
            raise ValueError("Filename is required")

        basename = filename.rsplit("/", 1)[-1]
        # The admission rule is the one predicate beside the constants
        # (``core/resources/services/file_service.admission_refusal``) — the
        # same question the manifest apply flow asks in its resolve stage,
        # so the two surfaces cannot drift from each other.
        refusal = admission_refusal(basename, data)
        if refusal is not None:
            raise ValueError(refusal)

        if preserve_structure and "/" in filename:
            safe = "/".join(
                p for p in filename.lstrip("/").split("/") if p and p != ".."
            )
            rel = f"{target_dir}/{safe}" if target_dir else safe
            final_name = safe.rsplit("/", 1)[-1]
        else:
            safe = basename
            rel = f"{target_dir}/{safe}" if target_dir else safe
            final_name = safe

        ctx = self._resolve_ctx(bot_id=bot_id, entity_id=entity_id)
        device_fs = self._device_fs(
            ctx, entity_type=entity_type, entity_id=entity_id,
            bot_id=bot_id, engine_type=engine_type,
        )
        await device_fs.write_file(self._logical(rel), data)

        size = len(data)
        return {
            "name": final_name,
            "path": rel,
            "absolute_path": self._logical(rel),
            "is_dir": False,
            "size": size,
            "size_human": FileNode._human_readable_size(size),
            "modified_at": datetime.now().isoformat(),
        }
