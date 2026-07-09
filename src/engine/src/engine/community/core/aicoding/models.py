"""AICoding workspace data models + tree-building helpers.

Engine-side mirror of the previous backend
``agentclaw.core.aicoding.models.workspace`` module. Used by
:class:`engine.community.core.aicoding.workspace_service.WorkspaceService` to
turn raw ``FileService.list_dir`` / ``git status --porcelain`` output
into the JSON shapes consumed by the workbench frontend.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ── Dataclasses ──────────────────────────────────────────────────────────


@dataclass
class FileTreeNode:
    """Tree node for the file-tree response."""

    name: str
    path: str
    is_dir: bool
    size: Optional[int] = None
    children: Optional[list["FileTreeNode"]] = None


@dataclass
class GitFileStatus:
    """Parsed git status --porcelain entry, scoped to a single project."""

    status: str  # added / modified / deleted / renamed / copied / untracked
    path: str  # path relative to project root
    old_path: Optional[str] = None  # only for renamed / copied


@dataclass
class DiffTreeNode:
    """Tree node for the git-diff response (per-project tree)."""

    name: str
    path: str
    is_dir: bool
    status: Optional[str] = None  # only for file nodes
    old_path: Optional[str] = None  # only for renamed / copied files
    children: Optional[list["DiffTreeNode"]] = None


@dataclass
class GitProjectDiff:
    """One project's diff tree, ready for serialization."""

    project: str
    tree: DiffTreeNode


@dataclass
class GitDiffTreeResult:
    """Result of :meth:`WorkspaceService.list_git_diff`."""

    session_id: str
    diff_head: list[GitProjectDiff]


@dataclass
class GitDiffResult:
    """Result of :meth:`WorkspaceService.get_file_diff`."""

    session_id: str
    project: str
    path: str
    diff: str


@dataclass
class FileContent:
    """Result of :meth:`WorkspaceService.preview_file`."""

    content: str
    size: int


# ── Parsing & tree-building ──────────────────────────────────────────────


_STATUS_MAP: dict[str, str] = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "??": "untracked",
}


def parse_porcelain_status(output: str) -> list[GitFileStatus]:
    """Parse ``git status --porcelain=v1 --untracked-files=all`` output."""
    results: list[GitFileStatus] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        xy = line[:2]
        path_part = line[3:]

        if xy == "!!":
            continue

        x, y = xy[0], xy[1]
        if x == "R" or y == "R":
            status = "renamed"
        elif x == "C" or y == "C":
            status = "copied"
        elif x == "A" or y == "A":
            status = "added"
        elif x == "D" or y == "D":
            status = "deleted"
        elif x == "?" and y == "?":
            status = "untracked"
        elif x == "M" or y == "M":
            status = "modified"
        else:
            status = _STATUS_MAP.get(x, "modified")

        old_path: Optional[str] = None
        file_path = path_part
        if status in ("renamed", "copied") and " -> " in path_part:
            old_path, file_path = path_part.split(" -> ", 1)

        results.append(
            GitFileStatus(status=status, path=file_path, old_path=old_path)
        )

    return results


_FILTERED_DIRS = {".git", "node_modules"}


def build_file_tree(files: list[dict]) -> list[FileTreeNode]:
    """Build a tree from flat ``FileService.list_dir`` entries.

    Each entry is expected to expose ``relative_path``, ``is_dir`` and
    ``size`` keys (matches :class:`engine.community.core.file.models.FileEntry`).
    Sorted by directories-first then alphabetical name.

    Filtering (e.g. ``.git``, ``node_modules``) is expected to happen
    upstream via ``exclude_dirs`` in ``FileService.list_dir``.
    """
    root_children: dict[str, FileTreeNode] = {}
    for entry in files:
        rel = entry.get("relative_path") or entry.get("path") or ""
        parts = rel.split("/")
        current_level: dict[str, FileTreeNode] | None = root_children
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            assert current_level is not None
            if part not in current_level:
                node_path = "/".join(parts[: i + 1])
                if is_last and not entry.get("is_dir", False):
                    current_level[part] = FileTreeNode(
                        name=part,
                        path=node_path,
                        is_dir=False,
                        size=entry.get("size"),
                        children=None,
                    )
                else:
                    current_level[part] = FileTreeNode(
                        name=part,
                        path=node_path,
                        is_dir=True,
                        children={},  # type: ignore[arg-type]
                    )
            current_level = current_level[part].children  # type: ignore[assignment]

    def _sort_and_clean(level: dict) -> list[FileTreeNode]:
        nodes = list(level.values())
        for node in nodes:
            if node.children and isinstance(node.children, dict):
                node.children = _sort_and_clean(node.children)
                if not node.children:
                    continue
        nodes.sort(key=lambda n: (not n.is_dir, n.name))
        return [n for n in nodes if not n.is_dir or n.children]

    return _sort_and_clean(root_children)


def build_diff_tree(files: list[GitFileStatus]) -> DiffTreeNode:
    """Build a tree from flat git status entries (single project)."""
    root_children: dict[str, DiffTreeNode] = {}
    for f in files:
        parts = f.path.split("/")
        current_level: dict[str, DiffTreeNode] | None = root_children
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            assert current_level is not None
            if part not in current_level:
                node_path = "/".join(parts[: i + 1])
                if is_last:
                    current_level[part] = DiffTreeNode(
                        name=part,
                        path=node_path,
                        is_dir=False,
                        status=f.status,
                        old_path=f.old_path,
                        children=None,
                    )
                else:
                    current_level[part] = DiffTreeNode(
                        name=part,
                        path=node_path,
                        is_dir=True,
                        children={},  # type: ignore[arg-type]
                    )
            if not is_last:
                current_level = current_level[part].children  # type: ignore[assignment]

    def _sort(level: dict) -> list[DiffTreeNode]:
        nodes = list(level.values())
        for node in nodes:
            if node.children and isinstance(node.children, dict):
                node.children = _sort(node.children)
        nodes.sort(key=lambda n: (not n.is_dir, n.name))
        return nodes

    sorted_children = _sort(root_children)
    if len(sorted_children) == 1:
        return sorted_children[0]
    return DiffTreeNode(
        name=".", path="", is_dir=True, children=sorted_children
    )


__all__ = [
    "FileTreeNode",
    "GitFileStatus",
    "DiffTreeNode",
    "GitProjectDiff",
    "GitDiffTreeResult",
    "GitDiffResult",
    "FileContent",
    "parse_porcelain_status",
    "build_file_tree",
    "build_diff_tree",
]
