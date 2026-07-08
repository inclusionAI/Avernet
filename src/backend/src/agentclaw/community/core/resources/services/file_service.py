"""
File service for managing data source files with directory support.
"""
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, TYPE_CHECKING, Union

from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem

logger = get_logger()

# ---------------------------------------------------------------------------
# Constants (inlined from services/openclawserver/server/config.py)
# ---------------------------------------------------------------------------
DATA_DIR = Path.home() / ".openclaw" / "workspace" / "data"
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
ALLOWED_EXTENSIONS = {
    ".txt", ".md", ".pdf", ".doc", ".docx",
    ".xls", ".xlsx", ".csv", ".json", ".jsonl", ".yaml", ".yml",
    ".py", ".js", ".ts", ".html", ".css", ".xml",
    ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".zip", ".tar", ".gz",
}


class FileNode:
    """Represents a file or directory node in the tree."""
    def __init__(self, name: str, path: str, is_dir: bool = False, size: int = 0,
                 modified_at: Optional[datetime] = None, children: Optional[List] = None):
        self.name = name
        self.path = path  # Relative path from data_dir
        self.is_dir = is_dir
        self.size = size
        self.modified_at = modified_at
        self.children = children or []

    def to_dict(self) -> Dict:
        result = {
            "name": self.name,
            "path": self.path,
            "is_dir": self.is_dir,
        }
        if not self.is_dir:
            result["size"] = self.size
            result["size_human"] = self._human_readable_size(self.size)
        if self.modified_at:
            result["modified_at"] = self.modified_at.isoformat()
        if self.children:
            result["children"] = [child.to_dict() for child in sorted(self.children,
                key=lambda x: (not x.is_dir, x.name.lower()))]
        return result

    @staticmethod
    def _human_readable_size(size_bytes: int) -> str:
        """Convert bytes to human readable format."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"


class FileService:
    """Service for file and directory operations."""

    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[FileService] Initialized with data_dir={data_dir}")

    def _resolve_path(self, rel_path: str) -> Path:
        """Resolve relative path to absolute path, ensuring it stays within data_dir."""
        # Normalize path and prevent directory traversal
        rel_path = rel_path.strip('/')
        if '..' in rel_path:
            raise ValueError("Invalid path: directory traversal not allowed")
        return self.data_dir / rel_path

    def _is_allowed_file(self, filename: str) -> bool:
        """Check if file extension is allowed."""
        ext = Path(filename).suffix.lower()
        return ext in ALLOWED_EXTENSIONS

    def _scan_directory(self, dir_path: Path, rel_path: str = "") -> FileNode:
        """Recursively scan a directory and build file tree."""
        name = dir_path.name if rel_path else "root"
        node = FileNode(
            name=name,
            path=rel_path or "",
            is_dir=True,
            modified_at=datetime.fromtimestamp(dir_path.stat().st_mtime)
        )

        try:
            for item in sorted(dir_path.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
                item_rel_path = f"{rel_path}/{item.name}" if rel_path else item.name

                if item.is_dir():
                    child = self._scan_directory(item, item_rel_path)
                    if child.children:  # Only add non-empty directories
                        node.children.append(child)
                else:
                    stat = item.stat()
                    node.children.append(FileNode(
                        name=item.name,
                        path=item_rel_path,
                        is_dir=False,
                        size=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime)
                    ))
        except Exception as e:
            logger.error("Error scanning directory %s: %s", dir_path, e)

        return node

    async def list_files(
        self, path: str = "", device_fs: "DeviceFileSystem | None" = None
    ) -> Dict:
        """List files and directories at given path.

        Transitional: when ``device_fs`` is injected the local else-branch routes
        through ``device_fs.list_dir`` instead of bare pathlib iterdir; without it
        the original bare-FS scan is the fallback (signature preserved).
        """
        target_path = self._resolve_path(path)

        if device_fs is not None:
            entries = await device_fs.list_dir(str(target_path), recursive=True)
            tree = FileNode(name=target_path.name or "root", path=path or "", is_dir=True)
            if entries:
                tree.children = [
                    FileNode(
                        name=e.get("name", ""),
                        path=f"{path}/{e['relative_path']}" if path else e.get("relative_path", e.get("name", "")),
                        is_dir=e.get("is_dir", False),
                        size=e.get("size", 0),
                    )
                    for e in entries
                    if not e.get("is_dir", False)
                ]
            return tree.to_dict()

        if not target_path.exists():
            return {"path": path, "is_dir": True, "children": []}

        if target_path.is_file():
            stat = target_path.stat()
            return FileNode(
                name=target_path.name,
                path=path,
                is_dir=False,
                size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime)
            ).to_dict()

        # Return directory tree
        tree = self._scan_directory(target_path, path)
        return tree.to_dict()

    async def list_flat(
        self, path: str = "", device_fs: "DeviceFileSystem | None" = None
    ) -> List[Dict]:
        """List files and directories flat at given path (non-recursive).

        Transitional: when ``device_fs`` is injected the local else-branch routes
        through ``device_fs.list_dir`` instead of bare pathlib iterdir; without it
        the original bare-FS scan is the fallback (signature preserved).
        """
        target_path = self._resolve_path(path)

        if device_fs is not None:
            entries = await device_fs.list_dir(str(target_path))
            if not entries:
                return []
            return [
                {
                    "name": e.get("name", ""),
                    "path": f"{path}/{e['name']}" if path else e.get("name", ""),
                    "is_dir": e.get("is_dir", False),
                }
                for e in entries
            ]

        if not target_path.exists() or not target_path.is_dir():
            return []

        items = []
        try:
            for item in sorted(target_path.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
                item_rel_path = f"{path}/{item.name}" if path else item.name
                stat = item.stat()

                if item.is_dir():
                    items.append({
                        "name": item.name,
                        "path": item_rel_path,
                        "is_dir": True,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    })
                else:
                    items.append({
                        "name": item.name,
                        "path": item_rel_path,
                        "is_dir": False,
                        "size": stat.st_size,
                        "size_human": FileNode._human_readable_size(stat.st_size),
                        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    })
        except Exception as e:
            logger.error("Error listing directory %s: %s", target_path, e)

        return items

    async def search_flat(
        self, keyword: str, base: str = "", max_results: int = 500
    ) -> List[Dict]:
        """Recursively search files/dirs by filename substring (case-insensitive).

        Returns a flat list (same dict shape as ``list_flat``) of matches under
        ``base``, with ``path`` relative to ``data_dir``. Dotfiles/dotdirs are
        skipped. Stops once ``max_results`` is reached. Symlinked dirs are not
        followed (``followlinks=False``) to avoid cycles / escaping the root.
        """
        root = self._resolve_path(base)
        if not root.exists() or not root.is_dir():
            return []

        kw = keyword.lower()
        results: List[Dict] = []

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # Prune dot-directories in place (also stops descent into them).
            pruned = (
                d for d in dirnames if not d.startswith(".")
            )
            dirnames[:] = sorted(pruned, key=str.lower)
            for dname in dirnames:
                if kw in dname.lower():
                    full = Path(dirpath) / dname
                    try:
                        mtime = datetime.fromtimestamp(full.stat().st_mtime).isoformat()
                    except OSError:
                        mtime = None
                    rel = os.path.relpath(full, self.data_dir).replace(os.sep, "/")
                    results.append({
                        "name": dname,
                        "path": rel,
                        "is_dir": True,
                        "modified_at": mtime,
                    })
                    if len(results) >= max_results:
                        return results
            for fname in sorted(filenames, key=str.lower):
                if fname.startswith(".") or kw not in fname.lower():
                    continue
                full = Path(dirpath) / fname
                try:
                    stat = full.stat()
                except OSError:
                    continue
                rel = os.path.relpath(full, self.data_dir).replace(os.sep, "/")
                results.append({
                    "name": fname,
                    "path": rel,
                    "is_dir": False,
                    "size": stat.st_size,
                    "size_human": FileNode._human_readable_size(stat.st_size),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
                if len(results) >= max_results:
                    return results

        return results

    async def list_tree_files(self, base: str = "") -> tuple[List[Dict], int]:
        """Recursively enumerate FILES under ``base`` (for archive download).

        Returns ``(files, total_size)`` where each entry is
        ``{"rel": <path relative to base>, "abs": <absolute fs path>, "size": int}``.
        Dotfiles/dotdirs are skipped; symlinked dirs are not followed.
        """
        root = self._resolve_path(base)
        if not root.exists() or not root.is_dir():
            return [], 0

        files: List[Dict] = []
        total = 0
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in filenames:
                if fname.startswith("."):
                    continue
                full = Path(dirpath) / fname
                try:
                    size = full.stat().st_size
                except OSError:
                    continue
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                files.append({"rel": rel, "abs": str(full), "size": size})
                total += size
        return files, total

    async def create_directory(
        self, path: str, device_fs: "DeviceFileSystem | None" = None
    ) -> Dict:
        """Create a new directory.

        Transitional: when ``device_fs`` is injected the local else-branch creates
        the directory via ``device_fs.write_file`` (uploading a .keep placeholder),
        mirroring the Arca strategy; without it the original bare-FS mkdir is the
        fallback (signature preserved).
        """
        target_path = self._resolve_path(path)
        logger.info(f"[FileService.create_directory] Creating directory: {target_path}")

        if device_fs is not None:
            keep_path = f"{target_path}/.keep"
            await device_fs.write_file(keep_path, b"")
            logger.info(f"[FileService.create_directory] Success via device_fs: {target_path}")
            return {
                "name": target_path.name,
                "path": path,
                "is_dir": True,
                "modified_at": datetime.now().isoformat(),
            }

        if target_path.exists():
            logger.warning(f"[FileService.create_directory] Path already exists: {target_path}")
            raise ValueError(f"Directory or file already exists: {path}")

        target_path.mkdir(parents=True, exist_ok=False)
        logger.info(f"[FileService.create_directory] Success: {target_path}")

        return {
            "name": target_path.name,
            "path": path,
            "is_dir": True,
            "modified_at": datetime.now().isoformat(),
        }

    async def delete_item(
        self, path: str, device_fs: "DeviceFileSystem | None" = None
    ) -> bool:
        """Delete a file or directory.

        Transitional: when ``device_fs`` is injected the local else-branch routes
        through ``device_fs.delete_tree`` instead of bare unlink/rmtree; without it
        the original bare-FS delete is the fallback (signature preserved).
        """
        target_path = self._resolve_path(path)
        logger.info(f"[FileService.delete_item] Deleting: {target_path}")

        if device_fs is not None:
            return await device_fs.delete_tree(str(target_path))

        if not target_path.exists():
            logger.warning(f"[FileService.delete_item] Path does not exist: {target_path}")
            return False

        try:
            if target_path.is_dir():
                shutil.rmtree(target_path)
                logger.info(f"[FileService.delete_item] Deleted directory: {target_path}")
            else:
                target_path.unlink()
                logger.info(f"[FileService.delete_item] Deleted file: {target_path}")
            return True
        except Exception as e:
            logger.error(f"[FileService.delete_item] Error deleting {target_path}: {e}")
            return False

    async def upload_file(
        self,
        *,
        data: bytes,
        filename: str,
        target_dir: str = "",
        preserve_structure: bool = False,
        device_fs: "DeviceFileSystem",
    ) -> Dict:
        """Upload a single file to specified directory.

        Args:
            data: Raw file bytes (read at the adapter layer).
            filename: Source filename. May contain '/' when preserve_structure is True.
            target_dir: The target directory path
            preserve_structure: If True, preserve relative path structure from filename
                               (for directory uploads where filename contains subdirs)
            device_fs: Device filesystem boundary to write through. The caller
                resolves it via ``DeviceFilesystemDispatcher.for_bot(...)``, which
                picks the Arca / local impl by the bot's device binding — so this
                method no longer branches on provider.
        """
        logger.info(f"[FileService.upload_file] Start: filename={filename}, target_dir={target_dir}, preserve_structure={preserve_structure}")
        logger.info(f"[FileService.upload_file] data_dir={self.data_dir}")

        if not filename:
            logger.error("[FileService.upload_file] Filename is required but was empty")
            raise ValueError("Filename is required")

        # Check file extension (only check the basename, not the full path)
        basename = os.path.basename(filename)
        if not self._is_allowed_file(basename):
            logger.warning(f"[FileService.upload_file] File type not allowed: {basename}")
            raise ValueError(f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

        # Resolve target directory
        target_dir_path = self._resolve_path(target_dir)
        logger.info(f"[FileService.upload_file] Resolved target_dir_path: {target_dir_path}")
        if not target_dir_path.exists():
            target_dir_path.mkdir(parents=True, exist_ok=True)
        elif not target_dir_path.is_dir():
            raise ValueError(f"Target path is not a directory: {target_dir}")

        # Handle relative path structure from directory uploads
        if preserve_structure and '/' in filename:
            # filename may contain relative path like "folder/subfolder/file.txt"
            relative_file_path = filename.lstrip('/')
            safe_relative_path = '/'.join(
                part for part in relative_file_path.split('/')
                if part and part != '..'  # Prevent directory traversal
            )
            file_path = target_dir_path / safe_relative_path
            # Ensure parent directories exist
            file_path.parent.mkdir(parents=True, exist_ok=True)
            rel_path = f"{target_dir}/{safe_relative_path}" if target_dir else safe_relative_path
            final_name = os.path.basename(safe_relative_path)
        else:
            # Simple upload - just use the filename
            safe_name = os.path.basename(filename)
            file_path = target_dir_path / safe_name
            rel_path = f"{target_dir}/{safe_name}" if target_dir else safe_name
            final_name = safe_name

        # Check file size
        if len(data) > MAX_FILE_SIZE:
            raise ValueError(f"File too large. Max size: {MAX_FILE_SIZE / 1024 / 1024}MB")

        # Write through the device-filesystem boundary. The dispatcher
        # already picked the right impl (Arca HTTP vs local FS), so there
        # is no provider branch here. For a local write the file lands on
        # disk and we report its real stat; for a remote (Arca) write the
        # path is not on local disk, so we fall back to the byte count and
        # current time — matching the prior per-branch behavior.
        await device_fs.write_file(str(file_path), data)
        try:
            stat = file_path.stat()
            size = stat.st_size
            modified_at = datetime.fromtimestamp(stat.st_mtime)
            logger.info(f"[FileService.upload_file] Success: saved to {file_path}, size={size}")
        except OSError:
            size = len(data)
            modified_at = datetime.now()
            logger.info(f"[FileService.upload_file] Success: pushed {file_path} ({size} bytes)")

        return FileNode(
            name=final_name,
            path=rel_path,
            is_dir=False,
            size=size,
            modified_at=modified_at,
        ).to_dict()

    async def upload_files(
        self,
        files: List[tuple[bytes, str]],
        target_dir: str = "",
        *,
        device_fs: "DeviceFileSystem",
    ) -> Dict:
        """Upload multiple files to specified directory.

        Args:
            files: List of (data, filename) tuples. Bytes read at the adapter layer.
            target_dir: The target directory path.
            device_fs: Device filesystem boundary to write each file through.

        Supports directory uploads where filename may contain relative paths.
        """
        results = []
        errors = []

        # Detect if this is a directory upload (any file has path separator)
        is_directory_upload = any('/' in (name or '') for _, name in files)

        for data, filename in files:
            try:
                result = await self.upload_file(
                    data=data,
                    filename=filename,
                    target_dir=target_dir,
                    preserve_structure=is_directory_upload,
                    device_fs=device_fs,
                )
                results.append({"success": True, "file": result})
            except Exception as e:
                errors.append({"success": False, "filename": filename, "error": str(e)})

        return {"uploaded": results, "errors": errors}

    async def get_file_info(self, path: str) -> Optional[Dict]:
        """Get file or directory information."""
        target_path = self._resolve_path(path)

        if not target_path.exists():
            return None

        stat = target_path.stat()
        is_dir = target_path.is_dir()

        result = {
            "name": target_path.name,
            "path": path,
            "is_dir": is_dir,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }

        if not is_dir:
            result["size"] = stat.st_size
            result["size_human"] = FileNode._human_readable_size(stat.st_size)

        return result

    async def get_file_path(
        self, path: str, device_fs: "DeviceFileSystem | None" = None
    ) -> "Union[Path, bytes, None]":
        """Get a file's local Path, or its bytes when device_fs is injected.

        Transitional: a device's filesystem has no local Path to hand back, so the
        injected else-branch returns file content via ``device_fs.read_file``
        (bytes | None). Without ``device_fs`` the original bare-FS behaviour returns
        an absolute Path (or None), preserving the download path; callers must
        handle either form.
        """
        target_path = self._resolve_path(path)
        if device_fs is not None:
            return await device_fs.read_file(str(target_path))
        return target_path if target_path.exists() and target_path.is_file() else None

    async def move_item(self, source_path: str, target_path: str, overwrite: bool = False) -> Dict:
        """Move or rename a file or directory."""
        source = self._resolve_path(source_path)
        target = self._resolve_path(target_path)

        if not source.exists():
            raise ValueError(f"Source not found: {source_path}")

        if target.exists():
            if not overwrite:
                raise ValueError(f"Target already exists: {target_path}")
            # Delete target if it exists and overwrite is True
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()

        # Ensure parent directory exists
        target.parent.mkdir(parents=True, exist_ok=True)

        shutil.move(str(source), str(target))

        return await self.get_file_info(target_path)


# Global file service instance
file_service = FileService()
