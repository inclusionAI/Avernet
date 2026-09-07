"""Claude Code file port: local I/O in the Engine/Claude shared filesystem.

The composition root supplies the permitted engine roots. Paths are already
engine-view addresses; enterprise host/NAS translation belongs to its provider.
No file operation connects to the chat relay.
"""

from __future__ import annotations

import asyncio
import shutil
import stat
from pathlib import Path


class _FilePortMixin:
    _file_roots: tuple[Path, ...]

    def _file_path(self, path: str, *, unlink: bool = False) -> Path:
        target = Path(path)
        if not path.strip() or not target.is_absolute() or ".." in target.parts:
            raise ValueError("File path must be absolute without parent traversal")
        # For removal, resolve ancestors but not the final symlink: unlink the
        # link itself, never recursively delete its target.
        resolved = target.parent.resolve() / target.name if unlink else target.resolve()
        if not any(resolved.is_relative_to(root) for root in self._file_roots):
            raise PermissionError("File path is outside the configured engine roots")
        return resolved

    async def file_upload(
        self, path: str, content_bytes: bytes, token: str | None = None
    ) -> dict:
        def write() -> dict:
            target = self._file_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            overwritten = target.exists()
            target.write_bytes(content_bytes)
            return {
                "path": str(target),
                "size": len(content_bytes),
                "overwritten": overwritten,
            }

        return await asyncio.to_thread(write)

    async def file_read(self, path: str, token: str | None = None) -> dict:
        def read() -> dict:
            target = self._file_path(path)
            if not target.is_file():
                raise FileNotFoundError("File does not exist")
            return {"path": str(target), "content": target.read_bytes()}

        return await asyncio.to_thread(read)

    async def file_remove(self, path: str, token: str | None = None) -> dict:
        def remove() -> dict:
            target = self._file_path(path, unlink=True)
            if target in self._file_roots:
                raise PermissionError("Cannot remove a configured engine root")
            is_directory = target.is_dir() and not target.is_symlink()
            if is_directory:
                shutil.rmtree(target)
            else:
                target.unlink()
            return {
                "target_path": str(target),
                "path_type": "directory" if is_directory else "file",
            }

        return await asyncio.to_thread(remove)

    async def file_rmtree(self, path: str, token: str | None = None) -> bool:
        await self.file_remove(path, token)
        return True

    async def file_list_dir(
        self,
        path: str,
        token: str | None = None,
        *,
        recursive: bool = False,
        exclude_dirs: set[str] | None = None,
    ) -> list[dict]:
        def list_files() -> list[dict]:
            root = self._file_path(path)
            if not root.exists():
                raise FileNotFoundError("Directory does not exist")
            if not root.is_dir():
                raise NotADirectoryError("Path is not a directory")
            excluded = exclude_dirs or set()
            entries: list[dict] = []

            def visit(directory: Path) -> None:
                for child in sorted(directory.iterdir()):
                    link_info = child.lstat()
                    is_link = stat.S_ISLNK(link_info.st_mode)
                    try:
                        checked = self._file_path(str(child))
                        info = checked.stat()
                        is_dir = stat.S_ISDIR(info.st_mode)
                        size = 0 if is_dir else info.st_size
                    except (FileNotFoundError, PermissionError):
                        if not is_link:
                            raise
                        # The link itself is inside the listed directory. Keep
                        # its own metadata visible for cleanup without probing
                        # an inaccessible target. A later read still fails, so
                        # package backups cannot silently omit unreadable data.
                        is_dir = False
                        size = link_info.st_size
                    if is_dir and child.name in excluded:
                        continue
                    entries.append(
                        {
                            "name": child.name,
                            "path": str(child),
                            "relative_path": child.relative_to(root).as_posix(),
                            "is_dir": is_dir,
                            "size": size,
                        }
                    )
                    if recursive and is_dir and not is_link:
                        visit(child)

            visit(root)
            return entries

        return await asyncio.to_thread(list_files)
