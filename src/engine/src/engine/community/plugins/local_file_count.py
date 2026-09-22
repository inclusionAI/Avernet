"""Shared file-count implementation for colocated Engine runtime filesystems."""
from pathlib import Path

from engine.community.core.file.models import CountFilesResult
from engine.community.plugins.file_count import count_files


class LocalFileCountService:
    def __init__(self, root: Path, allowed_roots: tuple[Path, ...]) -> None:
        self._root = root
        self._allowed_roots = allowed_roots

    async def count_files(self, path: str) -> CountFilesResult:
        result = await count_files(self._root, path, allowed_roots=self._allowed_roots)
        return CountFilesResult(**result)
