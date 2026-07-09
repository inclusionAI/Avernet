"""AICoding sessions HTTP routes — file-tree / preview / git-diff / file-diff.

Engine-direct endpoints used by the frontend workbench. Composes
:class:`engine.community.core.aicoding.workspace_service.WorkspaceService`
over :class:`EngineManager.file` and :class:`EngineManager.bash`.
"""
from engine.community.api.aicoding_sessions.router import router

__all__ = ["router"]
