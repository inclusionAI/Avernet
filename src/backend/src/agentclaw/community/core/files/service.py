"""BotFileService — teclaw workspace-file mutations (record + deliver).

For a teclaw bot, a workspace file only reaches the container if the delivered
``BotConfigArtifact`` references it, and that reference is composed from the
``ac_file`` rows. So every mutation here **records the row first**, then performs
the byte write/delete through the injected device filesystem (whose teclaw impl
redelivers the recomposed artifact). Recording before the write means the
redeliver already sees the row — no stale-by-one-edit. A write failure rolls the
row back so no ghost row outlives its bytes.

Paths are relative to the bot's **workspace root** (the file manager's root), so
``ac_file.path`` + the workspace dir == the device path the write targets and the
key compose emits as the artifact ref.
"""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from agentclaw.community.core.files.models import FileRecord
from agentclaw.community.core.files.repository.protocol import FileRepositoryProtocol
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem


logger = get_logger()


def resolve_upload_path(
    workspace_dir: Path, filename: str, target_dir: str, preserve_structure: bool
) -> tuple[str, str, str]:
    """Return ``(device_path, rel_path, name)`` for an upload.

    ``rel_path`` is relative to the workspace root (stored in ``ac_file.path``);
    ``device_path`` is the absolute device-view path the byte write targets.
    Mirrors ``FileService.upload_file``'s path logic (directory uploads preserve
    structure; ``..`` segments are stripped).
    """
    if preserve_structure and "/" in filename:
        safe_rel = "/".join(
            p for p in filename.lstrip("/").split("/") if p and p != ".."
        )
        rel_path = f"{target_dir}/{safe_rel}" if target_dir else safe_rel
        name = os.path.basename(safe_rel)
    else:
        name = os.path.basename(filename)
        rel_path = f"{target_dir}/{name}" if target_dir else name
    device_path = str(workspace_dir / rel_path)
    return device_path, rel_path, name


class BotFileService:
    """Per-bot teclaw workspace-file mutations, backed by ``ac_file``."""

    def __init__(
        self,
        *,
        repository: FileRepositoryProtocol,
        workspace_dir: Path,
        bot_id: str,
        entity_id: Optional[str],
        entity_type: Optional[str],
        engine_type: Optional[str],
        env: str,
    ) -> None:
        self._repo = repository
        self._workspace_dir = workspace_dir
        self._bot_id = bot_id
        self._entity_id = entity_id
        self._entity_type = entity_type
        self._engine_type = engine_type
        self._env = env

    async def upload_file(
        self,
        *,
        data: bytes,
        filename: str,
        target_dir: str = "",
        device_fs: "DeviceFileSystem",
        user_id: Optional[str] = None,
        created_by: Optional[str] = None,
        preserve_structure: bool = False,
    ) -> FileRecord:
        """Record the file row, then write the bytes; roll back on write failure."""
        device_path, rel_path, name = resolve_upload_path(
            self._workspace_dir, filename, target_dir, preserve_structure
        )
        parent = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""
        # Upsert: re-uploading the same path overwrites the bytes (same OSS key),
        # so drop any prior row(s) at this path first — else compose would emit a
        # duplicate ref and a later delete would leave a dangling one.
        while True:
            prior = self._repo.get_by_path(
                bot_id=self._bot_id, env=self._env, path=rel_path
            )
            if not prior:
                break
            self._repo.delete(prior.id)
        record = self._repo.create(
            {
                "bot_id": self._bot_id,
                "entity_id": self._entity_id,
                "entity_type": self._entity_type,
                "engine_type": self._engine_type,
                "env": self._env,
                "path": rel_path,
                "name": name,
                "parent_path": parent or None,
                "size": len(data),
                "mime_type": mimetypes.guess_type(name)[0],
                "source": "upload",
                "created_by": created_by,
                "user_id": user_id,
            }
        )
        try:
            # teclaw write_file = OSS put + redeliver; the row already exists, so
            # the recomposed artifact references this file.
            await device_fs.write_file(device_path, data)
        except Exception:
            # No backing bytes → drop the row we just wrote (no ghost record).
            # Guard the rollback so a delete failure can't mask the write error.
            try:
                self._repo.delete(record.id)
            except Exception:
                logger.warning(
                    "[BotFileService.upload_file] rollback delete failed id=%s",
                    record.id, exc_info=True,
                )
            raise
        logger.info(
            "[BotFileService.upload_file] recorded+wrote bot=%s path=%s size=%d",
            self._bot_id, rel_path, len(data),
        )
        return record

    async def delete_by_path(
        self, *, path: str, device_fs: "DeviceFileSystem"
    ) -> int:
        """Remove the row(s) at-or-under ``path``, then delete the bytes + redeliver.

        The file manager deletes by path without knowing file-vs-dir, so this
        removes the exact-path row (a file) **and** any subtree rows (a directory,
        matched by the ``path/`` prefix) — a dir isn't itself a row, only its files
        are. Removing the row before the device delete means the redeliver composes
        an artifact that no longer references the file. Legacy files with no row
        still get their bytes deleted (so files predating row-tracking are deletable).
        """
        exact = self._repo.get_by_path(
            bot_id=self._bot_id, env=self._env, path=path
        )
        subtree = self._repo.list_by_path_prefix(
            bot_id=self._bot_id, env=self._env, prefix=path.rstrip("/") + "/"
        )
        rows = ([exact] if exact else []) + subtree

        if not rows:
            # Legacy / untracked: still delete the bytes + redeliver.
            await device_fs.delete_file(str(self._workspace_dir / path))
            return 0

        # Remove ALL target rows first, THEN delete the bytes. The teclaw
        # device delete redelivers (recompose from DB); with every target row
        # already gone, each redeliver composes the final state — never a torn
        # mid-delete artifact. (Byte deletes still issue one redeliver each; a
        # batch "bytes-only then deliver once" is a future optimization.)
        for row in rows:
            self._repo.delete(row.id)
        for row in rows:
            await device_fs.delete_file(str(self._workspace_dir / row.path))
        logger.info(
            "[BotFileService.delete_by_path] removed %d row(s) bot=%s path=%s",
            len(rows), self._bot_id, path,
        )
        return len(rows)

    async def create_directory(
        self,
        *,
        path: str,
        device_fs: "DeviceFileSystem",
        user_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> FileRecord:
        """Materialize an (empty) directory by recording + writing a ``.keep``."""
        keep = f"{path.rstrip('/')}/.keep" if path else ".keep"
        return await self.upload_file(
            data=b"",
            filename=keep,
            target_dir="",
            device_fs=device_fs,
            user_id=user_id,
            created_by=created_by,
            preserve_structure=True,
        )
