"""HTTP routes for AICoding session workspace inspection.

Ten read-only endpoints, all served directly by the engine running inside
the aicoding container:

* ``GET /api/aicoding/sessions``                     — sessions list + run_status enrich
* ``GET /api/aicoding/sessions/file-tree``           — depth-limited workspace tree
* ``GET /api/aicoding/sessions/files/preview``       — file content (size-bounded)
* ``GET /api/aicoding/sessions/git-diff``            — changed files (tree per project)
* ``GET /api/aicoding/sessions/files/diff``          — unified diff for one file
* ``GET /api/aicoding/sessions/runs``                — devflow runs for a session
* ``GET /api/aicoding/sessions/phases``              — phase detail for a run
* ``GET /api/aicoding/sessions/runs/pull-requests``  — PR outputs for a session
* ``GET /api/aicoding/sessions/runs/issues``         — issue outputs for a session
* ``GET /api/aicoding/sessions/worktree-status``     — .worktree.json existence/status

The workspace-inspection endpoints (file-tree / files/preview / git-diff /
files/diff / worktree-status / runs / phases / runs/pull-requests /
runs/issues) accept an optional ``cwd`` query parameter: when provided it is
validated (``WorkspaceService.validate_cwd`` / ``_validate_cwd_prefix``) and used directly
as the session workspace root; when omitted, the legacy ``base/{session_id}``
derivation serves as a fallback (full backwards compatibility). For
``file-tree`` only, ``session_id`` and ``cwd`` are alternative workspace
locators and at least one must be non-empty; the other endpoints continue to
require ``session_id``.

Each handler composes a fresh
:class:`engine.community.core.aicoding.workspace_service.WorkspaceService` over
the currently active engine's :class:`FileService` + :class:`BashService`,
mirroring the lightweight pattern used by ``api/file/router.py`` and
``api/bash/router.py``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from engine.community.api.aicoding_sessions.schemas import (
    AicodingSessionListResponse,
    AicodingSessionSchema,
    DiffTreeNodeSchema,
    FileDiffResponse,
    FilePreviewData,
    FilePreviewResponse,
    FileTreeNodeSchema,
    FileTreeResponse,
    GitDiffResponse,
    GitProjectDiffSchema,
    IssueOutputInfo,
    PullRequestOutputInfo,
    RunPhaseStatusData,
    RunPhaseStatusResponse,
    SessionIssuesResponse,
    SessionPullRequestsResponse,
    SessionRunsResponse,
    WorktreeStatusResponse,
)
from engine.community.api.caps import check_capability
from engine.community.api.session.router import _session_to_dict
from engine.community.core.aicoding.models import DiffTreeNode, FileTreeNode
from engine.community.core.aicoding.runstatus_service import RunStatusService
from engine.community.core.aicoding.workspace_service import (
    DEFAULT_FILE_TREE_MAX_DEPTH,
    FilePreviewTooLargeError,
    WorkspaceService,
)
from engine.community.core.engine.capability import Capability
from engine.community.core.session.models import SessionListRequest

log = logging.getLogger("api-aicoding-sessions")

router = APIRouter(prefix="/api/aicoding/sessions", tags=["aicoding-sessions"])


# ── plugin accessors ─────────────────────────────────────────────────


def _workspace_service() -> WorkspaceService:
    """Build a service over the active engine's File + Bash plugins.

    Both plugin accessors raise :class:`CapabilityNotSupportedError`
    when the active engine doesn't declare the matching capability,
    which we surface as 501 below.
    """
    from engine.community.manager import EngineManager

    manager = EngineManager.get_instance()
    return WorkspaceService(file_plugin=manager.file, bash_plugin=manager.bash)


def _runstatus_service() -> RunStatusService:
    """Build a RunStatusService over the active engine's BashService.

    Mirrors :func:`_workspace_service` — no DI registration, just a
    lightweight wrapper that runs ``aix`` CLI inside the same container
    via the local subprocess BashService.
    """
    from engine.community.manager import EngineManager

    manager = EngineManager.get_instance()
    return RunStatusService(bash_plugin=manager.bash)


# ── serialization helpers ────────────────────────────────────────────


def _file_node_to_schema(node: FileTreeNode) -> FileTreeNodeSchema:
    return FileTreeNodeSchema(
        name=node.name,
        path=node.path,
        is_dir=node.is_dir,
        size=node.size,
        children=(
            [_file_node_to_schema(c) for c in node.children]
            if node.children
            else None
        ),
    )


def _diff_node_to_schema(node: DiffTreeNode) -> DiffTreeNodeSchema:
    return DiffTreeNodeSchema(
        name=node.name,
        path=node.path,
        is_dir=node.is_dir,
        status=node.status,
        old_path=node.old_path,
        children=(
            [_diff_node_to_schema(c) for c in node.children]
            if node.children
            else None
        ),
    )


# ── handlers ─────────────────────────────────────────────────────────


@router.get("/file-tree", response_model=FileTreeResponse)
async def list_file_tree(
    session_id: str | None = Query(
        None,
        description="可选：AICoding session ID；与 cwd 至少提供一个",
    ),
    cwd: str | None = Query(
        None,
        description="可选：前端直传工作目录绝对路径；与 session_id 至少提供一个",
    ),
    depth: int = Query(
        DEFAULT_FILE_TREE_MAX_DEPTH,
        ge=0,
        description="最大遍历层数；默认 3，0 表示返回所有层级",
    ),
) -> FileTreeResponse:
    """Return a depth-limited workspace tree (filtered and sorted)."""
    normalized_session_id = (session_id.strip() or None) if session_id else None
    normalized_cwd = (cwd.strip() or None) if cwd else None
    if not normalized_session_id and not normalized_cwd:
        raise HTTPException(
            status_code=400,
            detail="session_id and cwd cannot both be empty",
        )

    check_capability(Capability.FILE_LIST)
    service = _workspace_service()
    try:
        tree = await service.list_file_tree(
            normalized_session_id,
            normalized_cwd,
            max_depth=depth,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except NotADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    return FileTreeResponse(
        success=True,
        session_id=normalized_session_id,
        tree=[_file_node_to_schema(n) for n in tree],
    )


@router.get("/files/preview", response_model=FilePreviewResponse)
async def preview_file(
    session_id: str = Query(..., description="AICoding session ID"),
    path: str = Query(..., description="File path relative to workspace root"),
    cwd: str | None = Query(
        None,
        description="可选：前端直传工作目录绝对路径，"
        "缺省回退 base/{session_id}",
    ),
) -> FilePreviewResponse:
    """Read a single workspace file (size-bounded, traversal-safe)."""
    check_capability(Capability.FILE_READ)
    service = _workspace_service()
    try:
        content = await service.preview_file(session_id, path, cwd)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except NotADirectoryError as e:
        # cwd 直传指向文件（validate_cwd 阶段）→ 400。
        raise HTTPException(status_code=400, detail=str(e)) from e
    except IsADirectoryError as e:
        # preview 的 path（相对 workspace）指向目录 → 400。
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except FilePreviewTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e

    return FilePreviewResponse(
        success=True,
        session_id=session_id,
        data=FilePreviewData(content=content.content, size=content.size),
    )


@router.get("/git-diff", response_model=GitDiffResponse)
async def list_git_diff(
    session_id: str = Query(..., description="AICoding session ID"),
    cwd: str | None = Query(
        None,
        description="可选：前端直传工作目录绝对路径，"
        "缺省回退 base/{session_id}",
    ),
) -> GitDiffResponse:
    """Return per-project trees of changed files (modified/added/...)."""
    check_capability(Capability.BASH_EXEC)
    service = _workspace_service()
    try:
        result = await service.list_git_diff(session_id, cwd)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except NotADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    return GitDiffResponse(
        success=True,
        session_id=session_id,
        diff_head=[
            GitProjectDiffSchema(
                project=p.project, tree=_diff_node_to_schema(p.tree),
            )
            for p in result.diff_head
        ],
    )


@router.get("/files/diff", response_model=FileDiffResponse)
async def get_file_diff(
    session_id: str = Query(..., description="AICoding session ID"),
    project: str = Query(..., description="Project directory name"),
    path: str = Query(..., description="File path relative to project root"),
    old_path: str | None = Query(
        None, description="Original path for renamed/copied files"
    ),
    cwd: str | None = Query(
        None,
        description="可选：前端直传工作目录绝对路径，"
        "缺省回退 base/{session_id}",
    ),
) -> FileDiffResponse:
    """Return the unified diff for a single file (HEAD-based, with fallbacks)."""
    check_capability(Capability.BASH_EXEC)
    service = _workspace_service()
    try:
        result = await service.get_file_diff(
            session_id=session_id,
            project=project,
            file_path=path,
            old_path=old_path,
            cwd=cwd,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except NotADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        # Covers both "Not a git repository" and traversal/invalid-project.
        detail = str(e)
        status_code = 400
        raise HTTPException(status_code=status_code, detail=detail) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    return FileDiffResponse(
        success=True,
        session_id=session_id,
        project=result.project,
        path=result.path,
        diff=result.diff,
    )


# ── aicoding sessions + run_status / runs / phases ───────────────────


@router.get("", response_model=AicodingSessionListResponse)
async def list_sessions_with_run_status(
    user_id: str | None = Query(None, description="按用户过滤"),
    agent_id: str | None = Query(None, description="按 agent / bot 过滤"),
    limit: int = Query(20, description="返回条数上限"),
    offset: int = Query(0, description="偏移"),
) -> AicodingSessionListResponse:
    """API 4.1：返回 aicoding sessions 列表，每条追加 ``run_status`` 字段。"""
    check_capability(Capability.SESSION_LIST)
    from engine.community.manager import EngineManager

    manager = EngineManager.get_instance()
    try:
        sessions = await manager.session.list(
            SessionListRequest(
                user_id=user_id,
                agent_id=agent_id,
                limit=limit,
                offset=offset,
            )
        )
    except Exception as e:
        log.error("list_sessions_with_run_status failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e

    base = [_session_to_dict(s) for s in sessions]
    service = _runstatus_service()
    enriched = await service.enrich_with_run_status(base)
    return AicodingSessionListResponse(
        success=True,
        data=[AicodingSessionSchema(**item) for item in enriched],
    )


@router.get("/runs", response_model=SessionRunsResponse)
async def list_session_runs(
    session_id: str = Query(..., description="AICoding session ID"),
    cwd: str | None = Query(
        None,
        description="可选：前端直传工作目录绝对路径，"
        "缺省回退 base/{session_id}",
    ),
) -> SessionRunsResponse:
    """API 4.2：返回该 session 工作空间下的所有 devflow runs（透传 aix 输出）。"""
    check_capability(Capability.BASH_EXEC)
    service = _runstatus_service()
    try:
        runs = await service.get_session_runs(session_id, cwd)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except NotADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return SessionRunsResponse(success=True, session_id=session_id, runs=runs)


@router.get("/phases", response_model=RunPhaseStatusResponse)
async def get_run_phases(
    session_id: str = Query(..., description="AICoding session ID"),
    run_id: str = Query(..., description="Run ID（来自 /sessions/runs）"),
    cwd: str | None = Query(
        None,
        description="可选：前端直传工作目录绝对路径，"
        "缺省回退 base/{session_id}",
    ),
) -> RunPhaseStatusResponse:
    """API 4.3：返回某个 run 的 phase 详情（透传 ``aix run phase status --verbose``）。"""
    check_capability(Capability.BASH_EXEC)
    service = _runstatus_service()
    try:
        data = await service.get_run_phase_status(session_id, run_id, cwd)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except NotADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return RunPhaseStatusResponse(
        success=True,
        session_id=session_id,
        data=RunPhaseStatusData(**data),
    )


@router.get("/runs/pull-requests", response_model=SessionPullRequestsResponse)
async def list_session_pull_requests(
    session_id: str = Query(..., description="AICoding session ID"),
    cwd: str | None = Query(
        None,
        description="可选：前端直传工作目录绝对路径，"
        "缺省回退 base/{session_id}",
    ),
) -> SessionPullRequestsResponse:
    """API 4.4：返回 session 工作空间下所有 run 产出的 pull-request outputs。

    按 ``at`` (unix ms) 倒序排列。错误语义：

    - cwd 越界 / 非绝对 → 400
    - 命令执行失败 → 500（带 stderr）
    - JSON 解析失败 → 500

    注：本端点用 ``resolve_workspace``（仅前缀校验，不校验存在性），故 cwd
    指向不存在目录不会抛 ``FileNotFoundError``，而是交由 aix CLI 处理。
    """
    check_capability(Capability.BASH_EXEC)
    service = _runstatus_service()
    try:
        items = await service.get_session_pull_requests(session_id, cwd)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return SessionPullRequestsResponse(
        success=True,
        session_id=session_id,
        pull_requests=[PullRequestOutputInfo(**o) for o in items],
    )


@router.get("/runs/issues", response_model=SessionIssuesResponse)
async def list_session_issues(
    session_id: str = Query(..., description="AICoding session ID"),
    cwd: str | None = Query(
        None,
        description="可选：前端直传工作目录绝对路径，"
        "缺省回退 base/{session_id}",
    ),
) -> SessionIssuesResponse:
    """返回 session 工作空间下所有 run 产出的 issue outputs。

    执行 ``aix run output list --kind issue --json --filter <workspace>``，按
    ``at`` (unix ms) 倒序排列。不做 pull-request / issue 融合，单独返回
    ``issues`` 列表。
    """
    check_capability(Capability.BASH_EXEC)
    service = _runstatus_service()
    try:
        items = await service.get_session_issues(session_id, cwd)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return SessionIssuesResponse(
        success=True,
        session_id=session_id,
        issues=[IssueOutputInfo(**o) for o in items],
    )


# ── worktree status ────────────────────────────────────────────────────


@router.get("/worktree-status", response_model=WorktreeStatusResponse)
async def get_worktree_status(
    session_id: str = Query(..., description="AICoding session ID"),
    cwd: str | None = Query(
        None,
        description="可选：前端直传工作目录绝对路径，"
        "缺省回退 base/{session_id}",
    ),
) -> WorktreeStatusResponse:
    """Check .worktree.json existence and status in session workspace."""
    import json
    from pathlib import Path

    # worktree-status 走 resolve_workspace（前缀校验，不校验存在性），始终返 200。
    # cwd 直传但越界/非绝对 → resolve_workspace 抛 ValueError → 兜成 exists:false，
    # 不破坏既有客户端契约（缺失即 idle）。
    try:
        workspace = WorkspaceService.resolve_workspace(session_id, cwd)
    except ValueError as e:
        log.warning(
            "invalid cwd for worktree-status session=%s: %s", session_id, e
        )
        return WorktreeStatusResponse(
            session_id=session_id, exists=False, status="idle"
        )
    worktree_path = Path(workspace) / ".worktree.json"

    if not worktree_path.is_file():
        return WorktreeStatusResponse(
            session_id=session_id, exists=False, status="idle"
        )

    try:
        data = json.loads(worktree_path.read_text(encoding="utf-8"))
        return WorktreeStatusResponse(
            session_id=session_id,
            exists=True,
            status=data.get("status", "idle"),
        )
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read .worktree.json for session %s: %s", session_id, e)
        return WorktreeStatusResponse(
            session_id=session_id, exists=True, status="idle"
        )


__all__ = ["router"]
