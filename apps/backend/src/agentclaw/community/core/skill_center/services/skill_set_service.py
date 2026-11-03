"""Legacy SkillSet service; implementation is split by responsibility."""

import asyncio
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, List, Mapping, Optional, TypeVar

from agentclaw.community.core.devices.models import SynlinkMappingInfo
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.core.mcp.services._defaults import (
    get_default_mcp_config,
    get_default_mcp_server_codes,
    get_default_mcp_servers,
)
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilitySnapshot,
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.path_resolution import (
    canonical_pool_local_path,
)
from agentclaw.community.core.skill_center.policies.default_skill_set_selection import (
    DefaultSkillSetSelection,
    DefaultSkillSetSelectionPolicy,
)
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeDesiredState,
    resolve_effective_mcp_server_codes,
)
from agentclaw.community.core.skill_center.services.skill_service import SkillService
from agentclaw.community.core.skill_center.utils.skill_metadata_writer import (
    SkillSetMetadataWriter,
)
from agentclaw.community.core.workspace.constants import (
    DEFAULT_ENGINE_TYPE,  # noqa: E402
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin

from .skill_set_collections import SkillSetCollections
from .skill_set_context import (
    _T,
    SKILLS_DIR,
    SKILLS_LOCAL_DIR,
    SKILLS_REPO_DIR,
    _get_bot_paths,
    logger,
)
from .skill_set_defaults import SkillSetDefaultContext
from .skill_set_mcps import SkillSetMcpCollection
from .skill_set_projection import SkillSetRuntimeProjection
from .skill_set_runtime_content import SkillSetRuntimeContent
from .skill_set_views import SkillSetRuntimeViews

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.repository.protocols.bot import BotRepository
    from agentclaw.community.plugin_api.device_sync_dispatcher import (
        DeviceSyncDispatcher,
    )

class SkillSetService(
    SkillSetRuntimeContent,
    SkillSetDefaultContext,
    SkillSetRuntimeProjection,
    SkillSetCollections,
    SkillSetRuntimeViews,
    SkillSetMcpCollection,
):
    """Service for managing SkillSets."""

    def __init__(
        self,
        skill_repo: SkillRepository,
        skill_set_repo: SkillSetRepository,
        mcp_center: MCPCenterPlugin,
        mcp_config_service: MCPConfigService,
        skill_service: SkillService,
        bot_repo: "BotRepository",
        skills_dir: Path | None = None,
        repo_dir: Path | None = None,
        local_dir: Path | None = None,
        user_id: str | None = None,
        entity_id: str | None = None,
        bot_id: str | None = None,
        engine_type: str | None = None,
        runtime_engine_type: str | None = None,
        entity_type: str | None = None,
        resolver: "DeviceContextResolver | None" = None,
        device_sync_dispatcher: "DeviceSyncDispatcher | None" = None,
        mcp_sync_service=None,
        device_plugin: DeviceAccessor | None = None,
        ext_info_provider: Callable[[str], Mapping[str, Any] | None] | None = None,
        default_skill_set_selection_policy: DefaultSkillSetSelectionPolicy
        | None = None,
        *,
        path_factory: WorkspacePathFactory,
        device_owner_id: str | None = None,
        pool_layout_paths: Callable[
            [str, str, str],
            tuple[str, str, str] | None,
        ]
        | None = None,
        reader: BotCapabilityStateReaderProtocol | None = None,
    ):
        """
        Args:
            skill_repo: ``SkillRepository`` plugin (required).
            skill_set_repo: ``SkillSetRepository`` plugin (required).
            mcp_center: ``MCPCenterPlugin`` (required).
            mcp_config_service: ``MCPConfigService`` (required, supplied
                by ``SkillSetServiceFactory``).
            skill_service: ``SkillService`` instance scoped to this set's
                directories. The factory mints it after computing the
                directory layout.
            skills_dir: Directory for active skills (deprecated, use user_id/entity_id/bot_id/engine_type instead)
            repo_dir: Directory for skills repository (deprecated, use user_id/entity_id/bot_id/engine_type instead)
            local_dir: Directory for local skills (deprecated, use user_id/entity_id/bot_id/engine_type instead)
            user_id: User ID for determining paths (used to construct entity_id as staff_{user_id})
            entity_id: Entity ID (e.g., staff_xxx, proj_xxx, team_xxx or pure id)
            bot_id: Bot ID (default: "default")
            engine_type: Engine type (default: "moltis")
            entity_type: Entity type (e.g., staff, proj, team), used when entity_id is pure id
        """
        self.skill_repo = skill_repo
        self.skill_set_repo = skill_set_repo
        self.mcp_center = mcp_center
        self.mcp_config_service = mcp_config_service
        self.skill_service = skill_service
        self._bot_repo = bot_repo
        self._reader = reader

        self.bot_id = bot_id or "default"
        self.user_id = user_id
        self._device_owner_id = device_owner_id or user_id or entity_id
        self.entity_id = entity_id
        self.entity_type = entity_type or "staff"
        self.engine_type = engine_type or DEFAULT_ENGINE_TYPE
        self.runtime_engine_type = runtime_engine_type or self.engine_type

        self.device_plugin = device_plugin
        self._ext_info_provider = ext_info_provider

        # device_provider: dead field (only written, never read). Historically
        # looked up from ``DeviceAccessor.get_connection_info``. Kept as ``None``
        # to preserve the attribute name for any historical external readers;
        # the real provider routing is owned by :class:`DeviceContextResolver`.
        self.device_provider = None

        # is_desktop: look up bot_type from BotRepository — desktop bots route to the
        # agentbox engine-view path. Service bots' device_provider is also "baas" but
        # they're NOT desktop and must stay on the cloud OSS-view path.
        self.is_desktop = False

        if user_id or entity_id:
            try:
                owner_id = entity_id or user_id
                bot = self._bot_repo.get_by_id_and_entity(self.bot_id, owner_id)
                if bot and bot.get("bot_type") == "desktop":
                    self.is_desktop = True
            except Exception as e:
                logger.warning(
                    "[SkillSetService] bot_type lookup failed for "
                    "bot_id=%s owner_id=%s: %s — defaulting is_desktop=False",
                    self.bot_id,
                    owner_id,
                    e,
                )

        # Use new path structure if any path params provided, otherwise fall back to deprecated params
        if user_id or entity_id:
            self.skills_dir, self.repo_dir, self.local_dir = _get_bot_paths(
                path_factory=path_factory,
                user_id=user_id,
                entity_id=entity_id,
                bot_id=bot_id,
                engine_type=self.runtime_engine_type,
                entity_type=entity_type,
                is_desktop=self.is_desktop,
            )
        else:
            self.skills_dir = skills_dir or SKILLS_DIR
            self.repo_dir = repo_dir or SKILLS_REPO_DIR
            self.local_dir = local_dir or SKILLS_LOCAL_DIR

        self._pool_layout_paths = pool_layout_paths or (
            lambda _owner_id, _bot_id, _engine: None
        )
        effective_owner = user_id or entity_id
        if effective_owner is not None:
            pool_paths = self._pool_layout_paths(
                str(effective_owner),
                str(self.bot_id),
                self.runtime_engine_type,
            )
            if pool_paths is not None:
                active_path, local_path, repo_path = pool_paths
                self.skills_dir = Path(active_path)
                self.local_dir = Path(local_path)
                self.repo_dir = Path(repo_path)

        self._resolver = resolver
        self._device_sync_dispatcher = device_sync_dispatcher
        self._mcp_sync_service = mcp_sync_service
        self._default_skill_set_selection_policy = (
            default_skill_set_selection_policy or DefaultSkillSetSelectionPolicy()
        )

    def _use_host_skill_mapping_paths(self) -> bool:
        # Keep this existing legacy environment seam at its original location;
        # the split behavior modules consume the resolved decision only.
        from agentclaw.community.utils.env_utils import is_local_mode
        return is_local_mode() and not self.is_desktop


__all__ = [
    "asyncio",
    "time",
    "zlib",
    "datetime",
    "Path",
    "TYPE_CHECKING",
    "Any",
    "Callable",
    "List",
    "Mapping",
    "Optional",
    "TypeVar",
    "SynlinkMappingInfo",
    "DeviceAccessor",
    "DeviceSync",
    "get_default_mcp_config",
    "get_default_mcp_server_codes",
    "get_default_mcp_servers",
    "MCPConfigService",
    "SkillSetRepository",
    "SkillRepository",
    "BotCapabilitySnapshot",
    "BotCapabilityStateReaderProtocol",
    "LocalSkillNotFoundError",
    "SkillSetRuntimeReconcileError",
    "SkillService",
    "DefaultSkillSetSelection",
    "DefaultSkillSetSelectionPolicy",
    "canonical_pool_local_path",
    "RuntimeDesiredState",
    "resolve_effective_mcp_server_codes",
    "SkillSetMetadataWriter",
    "DEFAULT_ENGINE_TYPE",
    "WorkspacePathFactory",
    "get_logger",
    "MCPCenterPlugin",
    "SKILLS_DIR",
    "SKILLS_REPO_DIR",
    "SKILLS_LOCAL_DIR",
    "logger",
    "_T",
    "_get_bot_paths",
    "SkillSetService",
]
