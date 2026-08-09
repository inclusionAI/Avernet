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
from typing import Any

from injector import inject

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.config_compose.teclaw_paths import WORKSPACE_NS
from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError,
    DeviceContext,
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.resources.services.file_service import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
    FileNode,
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
        """Delete a file or directory; returns False when nothing was deleted."""
        ctx = self._resolve_ctx(bot_id=bot_id, entity_id=entity_id)
        device_fs = self._device_fs(
            ctx, entity_type=entity_type, entity_id=entity_id,
            bot_id=bot_id, engine_type=engine_type,
        )
        return await device_fs.delete_file(self._logical(path))

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
        if Path(basename).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValueError(
                f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )
        if len(data) > MAX_FILE_SIZE:
            raise ValueError(f"File too large. Max size: {MAX_FILE_SIZE / 1024 / 1024}MB")

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
