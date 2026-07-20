"""WorkspaceService — file tree + git diff + file preview for AICoding sessions.

Composes :class:`engine.community.core.file.protocol.FileService` and
:class:`engine.community.core.bash.protocol.BashService` for workspace file
and git operations.  The file-tree endpoint scans the container-local AICoding
workspace directly so it can prune root-level OSS mounts before traversal.

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

# AICoding workspace infrastructure directories that must never be exposed by
# the file-tree endpoint.  ``skills`` contains the OSS-mounted skills-repo and
# ``.repos`` contains shared worktree internals; both are root-only exclusions
# so project-local directories with the same names remain visible.
_FILE_TREE_ROOT_EXCLUDED_DIRS = {"skills", ".repos"}


def _resolve_workspace_base() -> str:
    """读取 workspace base。env 优先，未设置回落到硬编码。

    每次调用重新读取 env，便于测试通过 monkeypatch.setenv 覆盖。
    """
    raw = os.getenv("RELAY_DEFAULT_CWD", "").strip()
    return raw or CONTAINER_WORKSPACE_BASE


def _scan_file_tree_entries(workspace: str) -> list[dict]:
    """Return file-tree entries while pruning AICoding infrastructure mounts.

    The root ``skills`` directory contains an OSS-mounted ``skills-repo``.  On
    ossfs, merely walking that mount performs remote object listings and can
    take several seconds.  Pruning ``dirs`` in-place prevents ``os.walk`` from
    entering it at all.  ``.repos`` is likewise internal worktree storage.

    A file may disappear between directory listing and ``stat`` on eventually
    consistent mounts.  Such a race must not fail the entire file-tree request;
    the node is retained with an unknown size instead.
    """
    workspace_norm = os.path.normpath(workspace)
    entries: list[dict] = []

    for root, dirs, filenames in os.walk(
        workspace_norm,
        topdown=True,
        followlinks=False,
    ):
        root_norm = os.path.normpath(root)
        at_workspace_root = root_norm == workspace_norm
        dirs[:] = [
            dirname
            for dirname in dirs
            if dirname not in _FILTERED_DIRS
            and not (
                at_workspace_root
                and dirname in _FILE_TREE_ROOT_EXCLUDED_DIRS
            )
        ]

        for dirname in dirs:
            full_path = os.path.join(root, dirname)
            entries.append(
                {
                    "name": dirname,
                    "path": full_path,
                    "relative_path": os.path.relpath(full_path, workspace_norm),
                    "is_dir": True,
                    "size": 0,
                }
            )

        for filename in filenames:
            full_path = os.path.join(root, filename)
            try:
                size: int | None = os.stat(full_path).st_size
            except OSError as exc:
                log.debug(
                    "file-tree stat skipped for %s: %s",
                    full_path,
                    exc,
                )
                size = None
            entries.append(
                {
                    "name": filename,
                    "path": full_path,
                    "relative_path": os.path.relpath(full_path, workspace_norm),
                    "is_dir": False,
                    "size": size,
                }
            )

    return entries


def _strip_trailing_sep(path: str) -> str:
    """去末尾 ``/`` 但保留根 ``/``，全斜杠输入归一为 ``/``，永不返回空串。

    直接 ``rstrip("/")`` 会把根 ``/`` 与 POSIX 双斜杠 ``//``（:func:`os.path.normpath`
    不归一 ``//``）掏成空串 ``""``，在 :func:`WorkspaceService._validate_cwd_prefix`
    的前缀比较里 ``"" + "/" == "/"`` 会让任何绝对路径都满足 ``startswith("/")``，
    cwd 白名单完全失效（gemini code review PR#132 HIGH + 对抗验证发现的 ``//``
    回归）。
    """
    return path.rstrip("/") or "/"


def _allowed_cwd_roots() -> tuple[str, ...]:
    """读取 ``cwd`` 直传允许根（请求态白名单）。

    默认只放 :data:`CONTAINER_WORKSPACE_BASE`；env ``AICODING_CWD_ALLOW_ROOTS``
    （逗号分隔）可追加额外根（Rule 14：配置驱动）。每次调用重新读 env，
    便于测试用 monkeypatch 覆盖。

    normpath 后经 :func:`_strip_trailing_sep` 等于根 ``/`` 的条目（如 ``/``、
    ``//``、``/foo/..``、``/.``、``/..``）一律丢弃——把根当允许根等于关闭白名单
    （放行容器内所有绝对路径），与白名单语义相悖，视为无效配置，避免运维手滑 /
    占位符导致整盘开放；丢弃时打 warning。

    与 :class:`engine.community.core.bash.base.BaseBashService.exec` 的 exec 态
    白名单 ``ALLOWED_CWD_PREFIXES`` 互补：请求态拦端点入参 cwd，exec 态拦最终
    下沉到 subprocess 的 cwd。默认 ``CONTAINER_WORKSPACE_BASE`` 落在
    ``/home/admin/`` 下，两闸同向。
    """
    raw = os.getenv("AICODING_CWD_ALLOW_ROOTS", "").strip()
    extras: list[str] = []
    for piece in raw.split(","):
        stripped = piece.strip()
        if not stripped:
            continue
        normalized = os.path.normpath(stripped)
        if _strip_trailing_sep(normalized) == "/":
            log.warning(
                "ignoring root path in AICODING_CWD_ALLOW_ROOTS "
                "(would disable cwd allow-list): %r",
                stripped,
            )
            continue
        extras.append(normalized)
    return (CONTAINER_WORKSPACE_BASE,) + tuple(extras)


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
    def resolve_workspace(session_id: str, cwd: str | None = None) -> str:
        """Return the container-internal workspace root for a session.

        ``cwd`` 直传优先：非空时经 :meth:`_validate_cwd_prefix` 校验格式 + 允许根
        前缀（**不**校验存在性，供 ``worktree-status`` 这类"目录缺失仍返 200"
        的端点）后原样返回；为空则按 ``RELAY_DEFAULT_CWD`` /
        ``CONTAINER_WORKSPACE_BASE`` + ``session_id`` 拼接。``session_id`` 本身
        可能含 ``:``（例如 ``user:u1:session:s1:agent:b1``），按原样拼接。
        """
        if cwd:
            return WorkspaceService._validate_cwd_prefix(cwd)
        return f"{_resolve_workspace_base()}/{session_id}"

    @staticmethod
    def ensure_workspace_exists(session_id: str, cwd: str | None = None) -> str:
        """解析 + 校验 session 工作空间是否存在；不存在抛 ``FileNotFoundError``。

        ``cwd`` 直传优先：非空时经 :meth:`validate_cwd` 做完整校验（含存在性），
        cwd 指文件抛 ``NotADirectoryError``、不存在抛 ``FileNotFoundError``、
        越界/非绝对抛 ``ValueError``；为空则走旧 ``resolve_workspace(session_id)``
        + ``os.path.isdir`` 检查。返回校验通过的 workspace 绝对路径，便于调用方
        复用。Router 层应将 :class:`FileNotFoundError` 转换为 HTTP 404 返回前端。
        """
        if cwd:
            return WorkspaceService.validate_cwd(cwd)
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

    # ── cwd 直传校验（前端入参） ─────────────────────────────────────────

    @staticmethod
    def _validate_cwd_prefix(cwd: str) -> str:
        """校验 ``cwd`` 的格式 + 允许根前缀（**不**校验存在性），返回规范化绝对路径。

        供 :meth:`resolve_workspace` 使用——worktree-status 这类"目录缺失仍要
        返 200"的端点只需前缀校验，不能因目录不存在抛错。

        比较 ``cwd`` 与每个允许根时两侧都过 :func:`_strip_trailing_sep`，保证根
        ``/`` / ``//`` 不被掏成空串（否则 ``"" + "/" == "/"`` 会让任何绝对路径过关，
        见 gemini code review PR#132 HIGH）。
        """
        if not cwd or not cwd.strip():
            raise ValueError("cwd is required")
        normalized = os.path.normpath(cwd)
        if not os.path.isabs(normalized):
            raise ValueError(f"cwd must be absolute: {cwd!r}")
        normalized_norm = _strip_trailing_sep(normalized)
        for root in _allowed_cwd_roots():
            root_norm = _strip_trailing_sep(os.path.normpath(root))
            if normalized_norm == root_norm or normalized_norm.startswith(
                root_norm + "/"
            ):
                return normalized
        raise ValueError(f"cwd not allowed: {cwd!r}")

    @staticmethod
    def validate_cwd(cwd: str) -> str:
        """校验前端直传的 ``cwd``（含存在性），返回规范化绝对路径。

        供 :meth:`ensure_workspace_exists` 与各 router 入口使用。规则：

        1. 非空且为绝对路径，否则 ``ValueError``（→400）；
        2. normpath 后真实存在且是目录，否则 ``NotADirectoryError``（指向文件,
           →400）/ ``FileNotFoundError``（不存在，→404）；
        3. 必须落在允许根之一之下，否则 ``ValueError``（→400）。
        """
        normalized = WorkspaceService._validate_cwd_prefix(cwd)
        if os.path.isdir(normalized):
            return normalized
        if os.path.exists(normalized):
            raise NotADirectoryError(f"cwd not a directory: {cwd!r}")
        raise FileNotFoundError(f"cwd not found: {cwd!r}")

    # ── file tree ─────────────────────────────────────────────────────

    async def list_file_tree(
        self, session_id: str | None, cwd: str | None = None
    ) -> list[FileTreeNode]:
        """List workspace files recursively, excluding AICoding mounts."""
        normalized_session_id = session_id.strip() if session_id else None
        normalized_cwd = cwd.strip() if cwd else None
        if not normalized_session_id and not normalized_cwd:
            raise ValueError("session_id and cwd cannot both be empty")

        workspace = self.ensure_workspace_exists(
            normalized_session_id or "",
            normalized_cwd,
        )
        return build_file_tree(_scan_file_tree_entries(workspace))

    # ── file preview ──────────────────────────────────────────────────

    async def preview_file(
        self, session_id: str, path: str, cwd: str | None = None
    ) -> FileContent:
        """Read a single workspace file (size-bounded, traversal-safe)."""
        if not path or not path.strip():
            raise ValueError("path is required")

        workspace = self.ensure_workspace_exists(session_id, cwd)
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

    async def list_git_diff(
        self, session_id: str, cwd: str | None = None
    ) -> GitDiffTreeResult:
        """List changed files across all git projects in the workspace."""
        workspace = self.ensure_workspace_exists(session_id, cwd)

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
        cwd: str | None = None,
    ) -> GitDiffResult:
        """Single-file unified diff with untracked / renamed fallbacks."""
        if not project or "/" in project or project in ("", ".", ".."):
            raise ValueError(f"invalid project: {project!r}")

        workspace = self.ensure_workspace_exists(session_id, cwd)
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
    "_strip_trailing_sep",
    "_allowed_cwd_roots",
]
