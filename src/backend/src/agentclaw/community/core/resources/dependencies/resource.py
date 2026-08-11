"""DI factory for legacy resource services — transitional wrappers only.

This file isolates all imports from services/openclawserver so that
router.py stays free of legacy architecture dependencies.

NOTE: New resource operations (check-name, list, create-url, create-node)
use service_dep.py instead. This file covers file/folder/download/preview
operations that have not yet been migrated to the new plugin architecture.
"""
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.bot import BotRepository
    from agentclaw.community.core.repository.protocols.platform import ResourceRepositoryProtocol
    from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory


# ---- Workspace directory resolution (no DB) ----

def get_bot_workspace_dir(
    path_factory: "WorkspacePathFactory",
    entity_id: str,
    bot_id: str,
    engine_type: Optional[str] = None,
    entity_type: str = "staff",
) -> Path:
    """Return the workspace root directory for a given bot context.

    Callers should resolve engine_type via resolve_engine_for_bot() so the
    bot's active_engine is respected; when omitted here, falls back to
    DEFAULT_ENGINE_TYPE.
    """
    from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
    return path_factory.get_bot_workspace_dir(
        entity_id, bot_id, engine_type or DEFAULT_ENGINE_TYPE, entity_type,
    )


def get_file_service(data_dir: Path):
    """Return a FileService instance for local filesystem operations."""
    from agentclaw.community.core.resources.services.file_service import FileService
    return FileService(data_dir)


# ---- Legacy service factory (transitional — encapsulates openclawserver import) ----

def get_legacy_resource_service(
    repository: "ResourceRepositoryProtocol",
    bot_repo: "BotRepository",
    path_factory: "WorkspacePathFactory",
    entity_id: Optional[str] = None,
    bot_id: Optional[str] = None,
    engine_type: Optional[str] = None,
    entity_type: Optional[str] = None,
):
    """Return LegacyResourceService for file/folder/upload operations.

    This is a transitional wrapper. All openclawserver.services imports are
    isolated here so router.py stays clean. Callers must supply
    ``ResourceRepositoryProtocol``, ``BotRepository`` and
    ``WorkspacePathFactory`` (typically via ``Injected(...)`` in a FastAPI
    route signature) — the legacy service always operates on per-bot paths.
    """
    from agentclaw.community.core.resources.services.resource_service import ResourceService
    return ResourceService(
        repository=repository,
        bot_repo=bot_repo,
        path_factory=path_factory,
        entity_id=entity_id,
        bot_id=bot_id,
        engine_type=engine_type,
        entity_type=entity_type,
    )


def get_bot_data_dir(
    path_factory: "WorkspacePathFactory",
    entity_id: str,
    bot_id: str,
    engine_type: str,
    entity_type: str,
) -> Path:
    """Return the data directory for a given bot context (via WorkspacePathFactory)."""
    return path_factory.get_bot_data_dir(entity_id, bot_id, engine_type, entity_type)


def _get_file_service(data_dir: Path):
    """Return a FileService instance for local filesystem operations (transitional).

    This is a transitional wrapper. All openclawserver.services imports are
    isolated here so router.py stays clean.
    """
    from agentclaw.community.core.resources.services.file_service import FileService
    return FileService(data_dir)
