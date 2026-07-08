"""WorkspaceService — file tree + git diff + file preview for AICoding sessions.

Composes :class:`engine.community.core.file.protocol.FileService` and
:class:`engine.community.core.bash.protocol.BashService` to serve the four
``/api/aicoding/sessions/*`` endpoints.

The container workspace base path is ``/home/admin/.aicoding/workspace``
(matches the relay ``DEFAULT_CWD`` and the AiCodingFileService
prefix). Each session lives in ``{base}/{session_id}/`` with one or
more git project subdirectories under it.

Engine runs inside the aicoding container, so container paths are
used directly — no NFS prefix conversion is needed here.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from engine.community.core.aicoding.models import (
    FileContent,
    FileTreeNode,
    GitDiffResult,
    GitDiffTreeResult,
    GitProjectDiff,
    _FILTERED_DIRS,
    build_diff_tree,
    build_file_tree,
    parse_porcelain_status,
)

if TYPE_CHECKING:
    from engine.community.core.bash.protocol import BashService
    from engine.community.core.file.protocol import FileService


log = logging.getLogger("aicoding-workspace")

# 默认 workspace base —— 与 teamclaw-aicoding-relay 的 DEFAULT_CWD 对齐。
# 通过 ``RELAY_DEFAULT_CWD`` 环境变量可覆盖；未设置时使用此硬编码值。
CONTAINER_WORKSPACE_BASE = "/home/admin/.aicoding/workspace"

# 10 MiB cap for inline preview to keep responses bounded.
PREVIEW_MAX_BYTES = 10 * 1024 * 1024


def _resolve_workspace_base() -> str:
    """读取 workspace base。env 优先，未设置回落到硬编码。

    每次调用重新读取 env，便于测试通过 monkeypatch.setenv 覆盖。
    """
    raw = os.getenv("RELAY_DEFAULT_CWD", "").strip()
    return raw or CONTAINER_WORKSPACE_BASE


class WorkspaceService:
    """File tree + git operations for an AICoding session workspace."""

    def __init__(
        self,
        file_plugin: "FileService",
        bash_plugin: "BashService",
    ) -> None:
        self._file = file_plugin
        self._bash = bash_plugin

    # ── path helpers ──────────────────────────────────────────────────

    @staticmethod
    def resolve_workspace(session_id: str) -> str:
        """Return the container-internal workspace root for a session.

        Base 由 :func:`_resolve_workspace_base` 决定：``RELAY_DEFAULT_CWD``
        环境变量优先，未设置回落到硬编码 ``CONTAINER_WORKSPACE_BASE``。
        ``session_id`` 本身可能含 ``:``（例如
        ``user:u1:session:s1:agent:b1``），按原样拼接。
        """
        return f"{_resolve_workspace_base()}/{session_id}"

    @staticmethod
    def ensure_workspace_exists(session_id: str) -> str:
        """解析 + 校验 session 工作空间是否存在；不存在抛 ``FileNotFoundError``。

        返回校验通过的 workspace 绝对路径，便于调用方复用。Router 层应将
        :class:`FileNotFoundError` 转换为 HTTP 404 返回前端。
        """
        workspace = WorkspaceService.resolve_workspace(session_id)
        if not os.path.isdir(workspace):
            raise FileNotFoundError(
                f"AICoding session workspace not found: {workspace}"
            )
        return workspace

    @staticmethod
    def _ensure_within_workspace(full_path: str, workspace: str) -> None:
        """Guard against ``..`` / absolute-path traversal in user input."""
        normalized = os.path.normpath(full_path)
        workspace_norm = os.path.normpath(workspace)
        # rstrip trailing slash so equal prefixes match exactly
        prefix = workspace_norm.rstrip("/") + "/"
        if normalized != workspace_norm and not normalized.startswith(prefix):
            raise ValueError(f"path traversal detected: {full_path}")

    # ── file tree ─────────────────────────────────────────────────────

    async def list_file_tree(self, session_id: str) -> list[FileTreeNode]:
        """List workspace files recursively, grouped by project."""
        workspace = self.ensure_workspace_exists(session_id)
        result = await self._file.list_dir(
            workspace, recursive=True, exclude_dirs=_FILTERED_DIRS
        )
        entries = [
            {
                "name": f.name,
                "path": f.path,
                "relative_path": f.relative_path,
                "is_dir": f.is_dir,
                "size": f.size,
            }
            for f in result.files
        ]
        return build_file_tree(entries)

    # ── file preview ──────────────────────────────────────────────────

    async def preview_file(self, session_id: str, path: str) -> FileContent:
        """Read a single workspace file (size-bounded, traversal-safe)."""
        if not path or not path.strip():
            raise ValueError("path is required")

        workspace = self.ensure_workspace_exists(session_id)
        full_path = os.path.normpath(os.path.join(workspace, path.strip()))
        self._ensure_within_workspace(full_path, workspace)

        raw = await self._file.read(full_path)
        size = len(raw)
        if size > PREVIEW_MAX_BYTES:
            raise FilePreviewTooLargeError(
                f"file too large for preview: {size} bytes (limit "
                f"{PREVIEW_MAX_BYTES} bytes)"
            )

        return FileContent(
            content=raw.decode("utf-8", errors="replace"),
            size=size,
        )

    # ── git diff ──────────────────────────────────────────────────────

    async def list_git_diff(self, session_id: str) -> GitDiffTreeResult:
        """List changed files across all git projects in the workspace."""
        workspace = self.ensure_workspace_exists(session_id)

        # 1) Discover git projects under the workspace.
        #
        # ``.git`` may be a directory (standard repo) OR a file (worktree
        # pointer ``gitdir: ...``); relay materializes per-session worktrees
        # off ``<workspace>/.repos/<repo>``, so the file form is the norm
        # here, not the exception. ``-prune`` on ``.repos`` skips the shared
        # repo dir so the same repo isn't reported twice (once as the real
        # ``.git/`` dir, once as the session-side pointer file).
        find_cmd = (
            f'find "{workspace}" '
            f'-maxdepth 1 -type d -name .repos -prune -o '
            f'-maxdepth 2 -name .git \\( -type d -o -type f \\) -print'
        )
        find_res = await self._bash.exec(cmd=find_cmd, cwd=workspace, timeout=15)
        if find_res.exit_code != 0:
            log.warning(
                "list_git_diff find failed: cwd=%s stderr=%s",
                workspace,
                find_res.stderr,
            )
            return GitDiffTreeResult(session_id=session_id, diff_head=[])

        projects = self._parse_projects(find_res.stdout)

        # 2) git status per project, build tree per project.
        diff_head: list[GitProjectDiff] = []
        for project_name, project_path in projects:
            status_res = await self._bash.exec(
                # ``-c core.quotePath=false`` keeps non-ASCII paths (e.g. CJK
                # filenames) as raw UTF-8 instead of C-escaped + double-quoted
                # output like ``"\346\234\250\346\241\221.md"``.
                cmd=(
                    "git -c core.quotePath=false "
                    "status --porcelain=v1 --untracked-files=all"
                ),
                cwd=project_path,
                timeout=30,
            )
            if status_res.exit_code != 0:
                log.warning(
                    "git status failed for %s: %s",
                    project_name,
                    status_res.stderr,
                )
                continue
            # Do NOT ``.strip()`` here — ``git status --porcelain`` lines for
            # unstaged-only changes start with a leading space (e.g. ``" M
            # README.md"``). A whole-output strip would eat that leading space
            # on the first line, shifting ``line[3:]`` and dropping the first
            # filename character (README.md → EADME.md).
            output = status_res.stdout
            if not output.strip():
                continue
            statuses = parse_porcelain_status(output)
            if not statuses:
                continue
            tree = build_diff_tree(statuses)
            diff_head.append(GitProjectDiff(project=project_name, tree=tree))

        return GitDiffTreeResult(session_id=session_id, diff_head=diff_head)

    @staticmethod
    def _parse_projects(find_output: str) -> list[tuple[str, str]]:
        """Return ``[(project_name, project_path), ...]`` from ``find`` output."""
        projects: list[tuple[str, str]] = []
        for line in find_output.strip().splitlines():
            git_dir = line.strip()
            if not git_dir:
                continue
            # /.../workspace/{sid}/project-fe/.git → project_path, project-fe
            project_path = os.path.dirname(git_dir)
            project_name = os.path.basename(project_path)
            if not project_name:
                continue
            projects.append((project_name, project_path))
        return projects

    # ── file diff ─────────────────────────────────────────────────────

    async def get_file_diff(
        self,
        session_id: str,
        project: str,
        file_path: str,
        old_path: str | None = None,
    ) -> GitDiffResult:
        """Single-file unified diff with untracked / renamed fallbacks."""
        if not project or "/" in project or project in ("", ".", ".."):
            raise ValueError(f"invalid project: {project!r}")

        workspace = self.ensure_workspace_exists(session_id)
        project_path = os.path.normpath(os.path.join(workspace, project))
        self._ensure_within_workspace(project_path, workspace)

        if not os.path.isdir(project_path):
            raise FileNotFoundError(f"Project not found: {project}")
        # ``.git`` may be a directory (standard repo) or a file (linked
        # worktree pointer produced by ``git worktree add``).
        git_marker = os.path.join(project_path, ".git")
        if not (os.path.isdir(git_marker) or os.path.isfile(git_marker)):
            raise ValueError(f"Not a git repository: {project}")

        # Build primary git diff command.
        if old_path:
            cmd = f'git diff HEAD -- "{old_path}" "{file_path}"'
        else:
            cmd = f'git diff HEAD -- "{file_path}"'

        result = await self._bash.exec(cmd=cmd, cwd=project_path, timeout=30)
        diff_output = result.stdout

        # untracked fallback: git diff HEAD returns empty; show full file.
        if not diff_output.strip() and not old_path:
            fallback_cmd = f'git diff --no-index /dev/null "{file_path}"'
            fb = await self._bash.exec(
                cmd=fallback_cmd, cwd=project_path, timeout=30
            )
            # exit_code=1 here just means "there is a diff", not an error.
            diff_output = fb.stdout

        return GitDiffResult(
            session_id=session_id,
            project=project,
            path=file_path,
            diff=diff_output,
        )


class FilePreviewTooLargeError(Exception):
    """Raised when the requested preview exceeds ``PREVIEW_MAX_BYTES``."""


__all__ = [
    "WorkspaceService",
    "FilePreviewTooLargeError",
    "CONTAINER_WORKSPACE_BASE",
    "PREVIEW_MAX_BYTES",
    "_resolve_workspace_base",
]
