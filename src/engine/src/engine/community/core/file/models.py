"""
File plugin data models.

Carries operation results so the router can render the same JSON shape
the legacy direct-FS handlers did. Inputs are simple strings (paths)
because Pydantic request bodies stay engine-agnostic in the router.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UploadResult:
    """Outcome of :meth:`FileService.upload`."""

    target_path: str
    size: int
    overwritten: bool


@dataclass
class RemoveResult:
    """Outcome of :meth:`FileService.remove`.

    `path_type` is ``"file"`` or ``"directory"`` — surfaced so the
    frontend can tailor confirmation copy.
    """

    target_path: str
    path_type: str


@dataclass
class FileEntry:
    """One entry in a directory listing."""

    name: str
    path: str
    relative_path: str
    is_dir: bool
    size: int = 0


@dataclass
class ListDirResult:
    """Outcome of :meth:`FileService.list_dir`."""

    dir_path: str
    recursive: bool
    files: list[FileEntry] = field(default_factory=list)


__all__ = [
    "FileEntry",
    "ListDirResult",
    "RemoveResult",
    "UploadResult",
]
