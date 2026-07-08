"""BotFileServiceFactory — mints per-bot :class:`BotFileService` instances.

Lives in ``core/`` so ``adapters/`` can reference the factory type via
``Injected(BotFileServiceFactory)`` without importing the DI wiring layer. Wired
by ``FilesModule``; its deps (repository + path factory) are pure-core bindings.
"""
from __future__ import annotations

from typing import Optional

from injector import inject

from agentclaw.community.core.files.repository.protocol import FileRepositoryProtocol
from agentclaw.community.core.files.service import BotFileService
from agentclaw.community.core.resources.dependencies.resource import get_bot_workspace_dir
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.utils.env_utils import get_current_env


class BotFileServiceFactory:
    """Constructs :class:`BotFileService` bound to a specific bot context."""

    @inject
    def __init__(
        self,
        repository: FileRepositoryProtocol,
        path_factory: WorkspacePathFactory,
    ) -> None:
        self._repository = repository
        self._path_factory = path_factory

    def create(
        self,
        *,
        bot_id: str,
        entity_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
        env: Optional[str] = None,
    ) -> BotFileService:
        workspace_dir = get_bot_workspace_dir(
            self._path_factory, entity_id, bot_id, engine_type, entity_type
        )
        return BotFileService(
            repository=self._repository,
            workspace_dir=workspace_dir,
            bot_id=bot_id,
            entity_id=entity_id,
            entity_type=entity_type,
            engine_type=engine_type,
            env=env or get_current_env(),
        )
