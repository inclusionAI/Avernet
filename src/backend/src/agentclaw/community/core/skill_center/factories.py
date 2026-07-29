"""Skill-center service factories.

These mint per-request skill services from injector-supplied
singletons. They live in ``core/`` (not ``di/modules/``) so that
``api/`` routes and other ``core/`` services can reference the factory
types without importing the DI wiring layer.

They are wired by :class:`SkillCenterModule` via explicit
``@provider`` methods (not ``binder.bind``): the providers pass the
runtime-keyed device dispatchers — which legitimately live in the DI
layer because they bridge to ``plugins`` — so this module only
needs the dispatcher *types* under ``TYPE_CHECKING``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

from injector import inject

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.mcp.services.sync_service import MCPSyncService
from agentclaw.community.core.skill_center.services.git_sync import GitSyncService
from agentclaw.community.core.skill_center.services.repositories import (
    SkillCategoryRepository,
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.core.skill_center.services.skill_cache import MarketCache
from agentclaw.community.core.skill_center.path_resolution import (
    build_pool_local_path_adapter,
)
from agentclaw.community.core.skill_center.services.skill_parameter_service import (
    SkillParameterService,
)
from agentclaw.community.core.skill_center.services.skill_service import SkillService
from agentclaw.community.core.skill_center.services.skill_set_service import (
    SkillSetService,
)
from agentclaw.community.log import get_logger
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin

if TYPE_CHECKING:
    # Runtime-keyed device dispatchers stay in the DI layer (they bridge
    # to plugins). These factories are constructed via explicit
    # @provider methods, so the injector never introspects this class's
    # type hints — the string annotations below are never resolved at
    # runtime, making the TYPE_CHECKING import sufficient and the
    # core->di boundary intact.
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
    from agentclaw.community.di.modules.skill_center_module import (
        DeviceFilesystemDispatcher,
    )
    from agentclaw.community.core.devices.services.device_sync_dispatcher import (
        DeviceSyncDispatcher,
    )


logger = get_logger()


class SkillServiceFactory:
    """Mints :class:`SkillService` instances scoped to per-request paths.

    Holds the @inject-supplied singletons (``skill_repo``,
    ``skill_repo_sync``, ``category_repo``, ``member_repo``);
    ``create()`` forwards the caller's request-scoped paths and merges in
    the held deps.
    """

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        skill_repo_sync: SkillRepoSyncPlugin,
        category_repo: SkillCategoryRepository,
        device_fs_dispatcher: "DeviceFilesystemDispatcher",
        market_cache: MarketCache,
        git_sync_service_factory: Callable[[], GitSyncService],
        pool_layout_paths: Callable[
            [str, str, str],
            tuple[str, str, str] | None,
        ],
    ) -> None:
        self._skill_repo = skill_repo
        self._skill_repo_sync = skill_repo_sync
        self._category_repo = category_repo
        self._device_fs_dispatcher = device_fs_dispatcher
        self._market_cache = market_cache
        self._git_sync_service_factory = git_sync_service_factory
        self._pool_layout_paths = pool_layout_paths

    def resolve_pool_paths(
        self,
        entity_id: str,
        bot_id: str,
        engine_type: str,
    ) -> tuple[str, str, str] | None:
        """Resolve canonical Pool paths for a Bot when its layout owns IO."""

        return self._pool_layout_paths(entity_id, bot_id, engine_type)

    def create(
        self,
        active_dir: Optional[Path] = None,
        repo_dir: Optional[Path] = None,
        local_dir: Optional[Path] = None,
        global_repo_dir: Optional[Path] = None,
        device_fs_factory=None,
        local_skill_path_adapter: Optional[Callable[[str], str]] = None,
        local_skill_locator_adapter: Optional[
            Callable[[str], str]
        ] = None,
        entity_id: str | None = None,
        bot_id: str | None = None,
        engine_type: str | None = None,
    ) -> SkillService:
        uses_pool_paths = False
        if entity_id is not None and bot_id is not None:
            pool_paths = self.resolve_pool_paths(
                str(entity_id),
                str(bot_id),
                engine_type or "",
            )
            if pool_paths is not None:
                uses_pool_paths = True
                active_path, local_path, repo_path = pool_paths
                active_dir = Path(active_path)
                local_dir = Path(local_path)
                repo_dir = Path(repo_path)
                pool_local_adapter = build_pool_local_path_adapter(local_dir)
                local_skill_path_adapter = pool_local_adapter
                local_skill_locator_adapter = pool_local_adapter

        return SkillService(
            skill_repo=self._skill_repo,
            skill_repo_sync=self._skill_repo_sync,
            category_repo=self._category_repo,
            market_cache=self._market_cache,
            active_dir=active_dir,
            repo_dir=repo_dir,
            local_dir=local_dir,
            global_repo_dir=global_repo_dir,
            device_fs_factory=device_fs_factory or self._device_fs_dispatcher.for_bot,
            git_sync_service_factory=self._git_sync_service_factory,
            local_skill_path_adapter=local_skill_path_adapter,
            local_skill_locator_adapter=local_skill_locator_adapter,
            runtime_uses_pool_paths=uses_pool_paths,
        )


class SkillSetServiceFactory:
    """Mints :class:`SkillSetService` instances per-request.

    ``resolver`` / ``device_sync_dispatcher`` 走 lazy ``Callable`` thunk 注入,
    防止构造期 DI 循环:
      ``BotService → SkillSetServiceFactory → DeviceContextResolver
      → ArcaConnInfoBuilder → DeviceService → BotService``。
    """

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        skill_set_repo: SkillSetRepository,
        mcp_center: MCPCenterPlugin,
        mcp_config_service: MCPConfigService,
        skill_service_factory: SkillServiceFactory,
        resolver_provider: "Callable[[], DeviceContextResolver]",
        device_sync_dispatcher_provider: "Callable[[], DeviceSyncDispatcher]",
        mcp_sync_service: MCPSyncService,
        bot_repo: BotRepository,
        device_plugin: DeviceAccessor,
        path_factory: "WorkspacePathFactory",
        pool_layout_paths: Callable[
            [str, str, str],
            tuple[str, str, str] | None,
        ],
    ) -> None:
        self._skill_repo = skill_repo
        self._skill_set_repo = skill_set_repo
        self._mcp_center = mcp_center
        self._mcp_config_service = mcp_config_service
        self._skill_service_factory = skill_service_factory
        self._resolver_provider = resolver_provider
        self._device_sync_dispatcher_provider = device_sync_dispatcher_provider
        self._mcp_sync_service = mcp_sync_service
        self._bot_repo = bot_repo
        self._device_plugin = device_plugin
        self._path_factory = path_factory
        self._pool_layout_paths = pool_layout_paths

    def create(
        self,
        skills_dir: Optional[Path] = None,
        repo_dir: Optional[Path] = None,
        local_dir: Optional[Path] = None,
        user_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        engine_type: Optional[str] = None,
        entity_type: Optional[str] = None,
    ) -> SkillSetService:
        # Build a SkillSetService first to compute the resolved paths,
        # then mint a SkillService scoped to those paths and attach it.
        # We need the resolved paths to construct the SkillService; so
        # we do a two-step: construct a "draft" service to get paths,
        # then mint the SkillService and overwrite. Simpler: replicate
        # path resolution inline.
        # ── Resolve paths the same way SkillSetService does ──
        from agentclaw.community.core.skill_center.services.skill_set_service import (
            SKILLS_DIR,
            SKILLS_LOCAL_DIR,
            SKILLS_REPO_DIR,
            _get_bot_paths,
        )

        if user_id or entity_id:
            resolved_skills, resolved_repo, resolved_local = _get_bot_paths(
                path_factory=self._path_factory,
                user_id=user_id,
                entity_id=entity_id,
                bot_id=bot_id,
                engine_type=engine_type,
                entity_type=entity_type,
            )
        else:
            resolved_skills = skills_dir or SKILLS_DIR
            resolved_repo = repo_dir or SKILLS_REPO_DIR
            resolved_local = local_dir or SKILLS_LOCAL_DIR
        effective_owner = entity_id or user_id
        local_skill_path_adapter = None
        if effective_owner is not None and bot_id is not None:
            pool_paths = self._pool_layout_paths(
                str(effective_owner),
                str(bot_id),
                engine_type or "",
            )
            if pool_paths is not None:
                active_path, local_path, repo_path = pool_paths
                resolved_skills = Path(active_path)
                resolved_local = Path(local_path)
                resolved_repo = Path(repo_path)
                local_skill_path_adapter = build_pool_local_path_adapter(
                    resolved_local
                )

        skill_service = self._skill_service_factory.create(
            active_dir=resolved_skills,
            repo_dir=resolved_repo,
            local_dir=resolved_local,
            local_skill_path_adapter=local_skill_path_adapter,
        )

        return SkillSetService(
            skill_repo=self._skill_repo,
            skill_set_repo=self._skill_set_repo,
            mcp_center=self._mcp_center,
            mcp_config_service=self._mcp_config_service,
            skill_service=skill_service,
            bot_repo=self._bot_repo,
            skills_dir=skills_dir,
            repo_dir=repo_dir,
            local_dir=local_dir,
            user_id=user_id,
            entity_id=entity_id,
            bot_id=bot_id,
            engine_type=engine_type,
            entity_type=entity_type,
            resolver=self._resolver_provider(),
            device_sync_dispatcher=self._device_sync_dispatcher_provider(),
            mcp_sync_service=self._mcp_sync_service,
            device_plugin=self._device_plugin,
            path_factory=self._path_factory,
            pool_layout_paths=self._pool_layout_paths,
        )


class SkillParameterServiceFactory:
    """Mints :class:`SkillParameterService` for a (bot_id, user_id) pair.

    Computes the per-call ``device_fs`` via ``DeviceContextResolver`` +
    ``DeviceFilesystemDispatcher.dispatch(ctx)``, then constructs the
    service. Holds no singletons of its own — every dep is computed per
    call — but still lives as a ``@singleton`` for symmetry with the
    other factories.
    """

    @inject
    def __init__(
        self,
        resolver: "DeviceContextResolver",
        device_fs_dispatcher: "DeviceFilesystemDispatcher",
    ) -> None:
        self._resolver = resolver
        self._device_fs_dispatcher = device_fs_dispatcher

    async def create(
        self,
        bot_id: str,
        user_id: str,
        load_on_init: bool = True,
    ) -> SkillParameterService:
        ctx = self._resolver.resolve_for_bot(bot_id, user_id)
        device_fs = self._device_fs_dispatcher.dispatch(ctx)
        # teclaw does not use skill_parameters.json (the engine neither owns nor
        # consumes it), so disable engine read/write for it — load/save become
        # no-ops. arca/baas/local keep reading/writing the engine-absolute default.
        engine_io_enabled = ctx.provider != "teclaw"
        service = SkillParameterService(
            device_fs=device_fs, engine_io_enabled=engine_io_enabled
        )
        if load_on_init:
            await service.async_load()
        return service
