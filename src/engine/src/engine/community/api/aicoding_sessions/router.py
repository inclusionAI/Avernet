"""HTTP routes for AICoding session workspace inspection.

Four endpoints, all read-only, all served directly by the engine
running inside the aicoding container:

* ``GET /api/aicoding/sessions/file-tree``     — recursive workspace tree
* ``GET /api/aicoding/sessions/files/preview`` — file content (size-bounded)
* ``GET /api/aicoding/sessions/git-diff``      — changed files (tree per project)
* ``GET /api/aicoding/sessions/files/diff``    — unified diff for one file

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
    PullRequestOutputInfo,
    RunPhaseStatusData,
    RunPhaseStatusResponse,
    SessionPullRequestsResponse,
    SessionRunsResponse,
    WorktreeStatusResponse,
)
from engine.community.api.caps import check_capability
from engine.community.api.session.router import _session_to_dict
from engine.community.core.aicoding.models import DiffTreeNode, FileTreeNode
from engine.community.core.aicoding.runstatus_service import RunStatusService
from engine.community.core.aicoding.workspace_service import (
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
    session_id: str = Query(..., description="AICoding session ID"),
) -> FileTreeResponse:
    """Return the full workspace tree (recursive, filtered, sorted)."""
    check_capability(Capability.FILE_LIST)
    service = _workspace_service()
    try:
        tree = await service.list_file_tree(session_id)
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
        session_id=session_id,
        tree=[_file_node_to_schema(n) for n in tree],
    )


@router.get("/files/preview", response_model=FilePreviewResponse)
async def preview_file(
    session_id: str = Query(..., description="AICoding session ID"),
    path: str = Query(..., description="File path relative to workspace root"),
) -> FilePreviewResponse:
    """Read a single workspace file (size-bounded, traversal-safe)."""
    check_capability(Capability.FILE_READ)
    service = _workspace_service()
    try:
        content = await service.preview_file(session_id, path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except IsADirectoryError as e:
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
) -> GitDiffResponse:
    """Return per-project trees of changed files (modified/added/...)."""
    check_capability(Capability.BASH_EXEC)
    service = _workspace_service()
    try:
        result = await service.list_git_diff(session_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

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
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        # Covers both "Not a git repository" and traversal/invalid-project.
        detail = str(e)
        status_code = 400
        raise HTTPException(status_code=status_code, detail=detail) from e

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
) -> SessionRunsResponse:
    """API 4.2：返回该 session 工作空间下的所有 devflow runs（透传 aix 输出）。"""
    check_capability(Capability.BASH_EXEC)
    service = _runstatus_service()
    try:
        runs = await service.get_session_runs(session_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return SessionRunsResponse(success=True, session_id=session_id, runs=runs)


@router.get("/phases", response_model=RunPhaseStatusResponse)
async def get_run_phases(
    session_id: str = Query(..., description="AICoding session ID"),
    run_id: str = Query(..., description="Run ID（来自 /sessions/runs）"),
) -> RunPhaseStatusResponse:
    """API 4.3：返回某个 run 的 phase 详情（透传 ``aix run phase status --verbose``）。"""
    check_capability(Capability.BASH_EXEC)
    service = _runstatus_service()
    try:
        data = await service.get_run_phase_status(session_id, run_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return RunPhaseStatusResponse(
        success=True,
        session_id=session_id,
        data=RunPhaseStatusData(**data),
    )


@router.get("/runs/pull-requests", response_model=SessionPullRequestsResponse)
async def list_session_pull_requests(
    session_id: str = Query(..., description="AICoding session ID"),
) -> SessionPullRequestsResponse:
    """API 4.4：返回 session 工作空间下所有 run 产出的 pull-request outputs。

    按 ``at`` (unix ms) 倒序排列。错误语义：

    - workspace 不存在 → 404
    - 命令执行失败 → 500（带 stderr）
    - JSON 解析失败 → 500
    """
    check_capability(Capability.BASH_EXEC)
    service = _runstatus_service()
    try:
        items = await service.get_session_pull_requests(session_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return SessionPullRequestsResponse(
        success=True,
        session_id=session_id,
        pull_requests=[PullRequestOutputInfo(**o) for o in items],
    )


# ── worktree status ────────────────────────────────────────────────────


@router.get("/worktree-status", response_model=WorktreeStatusResponse)
async def get_worktree_status(
    session_id: str = Query(..., description="AICoding session ID"),
) -> WorktreeStatusResponse:
    """Check .worktree.json existence and status in session workspace."""
    import json
    from pathlib import Path

    workspace = WorkspaceService.resolve_workspace(session_id)
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
