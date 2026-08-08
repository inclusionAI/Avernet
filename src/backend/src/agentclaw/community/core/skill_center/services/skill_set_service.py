"""SkillSet service layer for managing capability sets.

Migrated from: services/openclawserver/server/services/skill_set_service.py
"""
import asyncio
import json
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, List, Optional

from agentclaw.community.core.devices.models import SynlinkMappingInfo
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
    from agentclaw.community.di.modules.skill_center_module import (
        DeviceFilesystemDispatcher,
    )
    from agentclaw.community.core.devices.services.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.core.mcp.services._defaults import (
    get_default_mcp_config,
    get_default_mcp_server_codes,
    get_default_mcp_servers,
)
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.skill_center.services.repositories import (
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.core.skill_center.services.skill_service import SkillService
from agentclaw.community.core.skill_center.path_resolution import (
    canonical_pool_local_path,
)
from agentclaw.community.core.skill_center.utils.skill_metadata_writer import SkillSetMetadataWriter
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE  # noqa: E402
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditGuard,
    SkillsPoolEditPausedError,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin


# Default path constants — ARCA container fallback only. 业务 caller 已经走
# _get_bot_paths(path_factory, ...) 按 bot 隔离;这几个常量只兜底 admin /
# migration 场景,历史上这条链路只在 ARCA 容器内跑。
SKILLS_DIR = Path("/home/admin/.openclaw/workspace/skills")
SKILLS_REPO_DIR = SKILLS_DIR / "skills-repo"
SKILLS_LOCAL_DIR = SKILLS_DIR / "skills-local"

logger = get_logger()


def _get_bot_paths(
    path_factory: WorkspacePathFactory,
    user_id: str | None = None,
    entity_id: str | None = None,
    bot_id: str | None = None,
    engine_type: str | None = None,
    entity_type: str | None = None,
    is_desktop: bool = False,
):
    """Get bot paths using new directory structure.

    Priority:
    1. If entity_id, bot_id, engine_type provided: use them directly
    2. If user_id provided: construct entity_id from user_id (staff_{user_id})
    3. Otherwise: use defaults (staff_default/default/moltis)

    Args:
        user_id: User ID (used to construct entity_id if entity_id not provided)
        entity_id: Entity ID (e.g., staff_xxx, proj_xxx, team_xxx or pure id)
        bot_id: Bot ID (default: "default")
        engine_type: Engine type (default: "moltis")
        entity_type: Entity type (e.g., staff, proj, team), used when entity_id is pure id


        is_desktop: True when ``ac_bots.bot_type == "desktop"`` — selects the
            agentbox engine-view path. Defaults to False so non-desktop bots
            (personal/service) keep the cloud OSS-view path.

    Returns:
        Tuple of (skills_dir, repo_dir, local_dir)
    """
    factory = path_factory

    # Priority 1: Use provided entity_id/bot_id/engine_type
    if entity_id and bot_id and engine_type:
        # Extract entity_type and pure entity_id from entity_id format
        if entity_id.startswith("staff_"):
            effective_entity_type = "staff"
            effective_entity_id = entity_id[6:]  # Remove "staff_" prefix
        elif entity_id.startswith("proj_"):
            effective_entity_type = "proj"
            effective_entity_id = entity_id[5:]  # Remove "proj_" prefix
        elif entity_id.startswith("team_"):
            effective_entity_type = "team"
            effective_entity_id = entity_id[5:]  # Remove "team_" prefix
        else:
            # Pure entity_id, use provided entity_type or default to "staff"
            effective_entity_type = entity_type if entity_type else "staff"
            effective_entity_id = entity_id
        effective_bot_id = bot_id
        effective_engine = engine_type
    # Priority 2: Construct from user_id
    elif user_id:
        effective_entity_type = "staff"
        effective_entity_id = user_id
        effective_bot_id = "default"
        effective_engine = DEFAULT_ENGINE_TYPE
    # Priority 3: Use defaults
    else:
        effective_entity_type = "staff"
        effective_entity_id = "default"
        effective_bot_id = "default"
        effective_engine = DEFAULT_ENGINE_TYPE

    # Use new bolt directory methods with separate entity_type and entity_id
    skills_dir = factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)

    local_dir = factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)
    repo_dir = factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)

    return skills_dir, repo_dir, local_dir


logger = get_logger()


class SkillSetService:
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
        entity_type: str | None = None,
        resolver: "DeviceContextResolver | None" = None,
        device_sync_dispatcher: "DeviceSyncDispatcher | None" = None,
        mcp_sync_service=None,
        device_plugin: DeviceAccessor | None = None,
        *,
        path_factory: WorkspacePathFactory,
        pool_layout_paths: Callable[
            [str, str, str],
            tuple[str, str, str] | None,
        ]
        | None = None,
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

        self.bot_id = bot_id or "default"
        self.user_id = user_id
        self.entity_id = entity_id
        self.entity_type = entity_type or "staff"
        self.engine_type = engine_type or DEFAULT_ENGINE_TYPE

        self.device_plugin = device_plugin

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
                owner_id = user_id or entity_id
                bot = self._bot_repo.get_by_id_and_owner(self.bot_id, owner_id)
                if bot and bot.get("bot_type") == "desktop":
                    self.is_desktop = True
            except Exception as e:
                logger.warning(
                    "[SkillSetService] bot_type lookup failed for "
                    "bot_id=%s owner_id=%s: %s — defaulting is_desktop=False",
                    self.bot_id, owner_id, e,
                )

        # Use new path structure if any path params provided, otherwise fall back to deprecated params
        if user_id or entity_id:
            self.skills_dir, self.repo_dir, self.local_dir = _get_bot_paths(
                path_factory=path_factory,
                user_id=user_id,
                entity_id=entity_id,
                bot_id=bot_id,
                engine_type=engine_type,
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
                self.engine_type,
            )
            if pool_paths is not None:
                active_path, local_path, repo_path = pool_paths
                self.skills_dir = Path(active_path)
                self.local_dir = Path(local_path)
                self.repo_dir = Path(repo_path)

        self.CURRENT_SET_FILE = self.skills_dir / ".current_skill_set"

        self._resolver = resolver
        self._device_sync_dispatcher = device_sync_dispatcher
        self._mcp_sync_service = mcp_sync_service

    def _get_current_active_skill_set_id(self) -> str | None:
        """Get currently active skill set ID from database (is_active=1)."""
        try:
            # 优先从数据库查询
            active_set = self.skill_set_repo.get_active_skill_set(
                user_id=self.user_id or self.entity_id,
                bolt_id=self.bot_id,
                engine_type=self.engine_type
            )
            if active_set:
                return str(active_set.get('id'))

            # 兼容：如果数据库没有记录，尝试从文件读取（旧逻辑）
            if self.CURRENT_SET_FILE.exists():
                data = json.loads(self.CURRENT_SET_FILE.read_text())
                return data.get("skill_set_id")
        except Exception as e:
            logger.warning(f"Error getting current active skill set: {e}")
        return None

    def _sync_symlinks_to_device_if_needed(self, user_id: str | None = None) -> bool:
        """如果需要，同步软链配置到设备。

        将当前激活技能集的软链配置同步到远程设备或本地文件系统。
        即使软链列表为空也会同步（空列表表示清空设备上的软链）。

        Args:
            user_id: 用户ID

        Returns:
            True if sync attempted, False otherwise
        """
        try:
            # 获取当前激活技能集的软链配置（包括空列表，表示清空）
            symlinks = self.get_symlink_mappings(
                user_id=user_id,
                bolt_id=self.bot_id
            )

            # 通过 DeviceSyncPlugin 同步到设备 — 经 resolver + dispatcher 收口
            effective_user_id = user_id or self.entity_id or "default"
            ctx = self._resolver.resolve_for_bot(self.bot_id, effective_user_id)
            device_sync = self._device_sync_dispatcher.dispatch(ctx)

            logger.info(f"[_sync_symlinks_to_device_if_needed] Syncing {len(symlinks)} symlinks to device (bot_id={self.bot_id})")

            symlinks_dict = [sm.to_dict() for sm in symlinks]
            sync_result = device_sync.sync_symlinks(symlinks_dict)

            if sync_result.get("success"):
                logger.info(f"[_sync_symlinks_to_device_if_needed] Sync successful: {sync_result.get('message')}")
                return True
            else:
                logger.error(f"[_sync_symlinks_to_device_if_needed] Sync failed: {sync_result.get('message')}")
                return False

        except Exception as e:
            logger.warning(f"[_sync_symlinks_to_device_if_needed] Failed to sync symlinks: {e}", exc_info=True)
            return False

    def sync_runtime(self) -> bool:
        """Reconcile this Bot's desired Skill mapping to its runtime."""
        return self._sync_symlinks_to_device_if_needed(self.user_id or self.entity_id)

    def _validate_name(self, name: str) -> None:
        """Validate skill set name (cannot contain underscore)."""
        if "_" in name:
            raise ValueError("Skill set name cannot contain underscore '_'")

    def create_skill_set(
        self,
        name: str,
        description: str | None = None,
        user_id: str | None = None,
        is_default: bool = False,
        bolt_id: str | None = None
    ) -> dict:
        """Create a new skill set.

        Args:
            name: Skill set name (cannot contain underscore)
            description: Optional description
            user_id: User ID (optional in Phase 1)
            is_default: Whether this is the default skill set
            bolt_id: Bot ID, defaults to self.bot_id if not provided

        Returns:
            Created SkillSet dict

        Raises:
            ValueError: If name contains underscore or already exists
        """
        self._validate_name(name)

        effective_bolt_id = bolt_id if bolt_id else self.bot_id

        # Check if name already exists for the same user (排除已删除 Bot 的技能集)
        if hasattr(self.skill_set_repo, 'list_all_exclude_deleted'):
            existing_sets = self.skill_set_repo.list_all_exclude_deleted(user_id, bolt_id=effective_bolt_id, engine_type=self.engine_type)
        else:
            existing_sets = self.skill_set_repo.list_all(user_id, bolt_id=effective_bolt_id, engine_type=self.engine_type)
        for existing in existing_sets:
            if existing.get('name') == name:
                raise ValueError(f"Skill set name '{name}' already exists for this user")

        # 检查是否有已删除 Bot 的同名技能集，如果有就复用（避免唯一约束冲突）
        existing_deleted = None
        if hasattr(self.skill_set_repo, 'get_skill_set_by_name_include_deleted'):
            existing_deleted = self.skill_set_repo.get_skill_set_by_name_include_deleted(name, user_id, effective_bolt_id)

        if existing_deleted:
            update_data = {
                'bolt_id': effective_bolt_id,
                'engine_type': self.engine_type,
                'description': description,
                'is_default': is_default,
                'is_active': 1,
                'gmt_modified': datetime.utcnow()
            }
            logger.info(f"[create_skill_set] Reusing skill set from deleted bot: {name}")
            skill_set = self.skill_set_repo.update(existing_deleted['id'], update_data)
        else:
            skill_set_data = {
                'name': name,
                'description': description,
                'user_id': user_id,
                'is_default': is_default,
                'is_active': 1,
                'is_builtin': False,
                'bolt_id': effective_bolt_id,
                'engine_type': self.engine_type,
                'gmt_created': datetime.utcnow(),
                'gmt_modified': datetime.utcnow()
            }
            skill_set = self.skill_set_repo.create(skill_set_data)

        # Update metadata file
        logger.info(f"[create_skill_set] Writing metadata for new skill set: {skill_set['name']}")
        SkillSetMetadataWriter(skill_set_repo=self.skill_set_repo, skill_repo=self.skill_repo, skills_dir=self.skills_dir, user_id=user_id, bot_id=self.bot_id).write_metadata()

        return skill_set

    def get_skill_set(self, skill_set_id: str, user_id: str | None = None) -> dict | None:
        """Get a skill set by ID.

        Args:
            skill_set_id: Skill set ID
            user_id: User ID for permission check (optional in Phase 1)

        Returns:
            SkillSet dict or None
        """
        return self.skill_set_repo.get_by_id(skill_set_id)

    def get_user_default_enabled(self, user_id: str | None, bolt_id: str) -> int | None:
        """查询用户对默认能力集的启用状态

        Returns:
            None: 无记录，表示默认启用
            1: 已启用
            0: 已禁用
        """
        return self.skill_set_repo._get_user_default_enabled(user_id, bolt_id, engine_type=self.engine_type)

    def list_skill_sets(self, user_id: str | None = None, bolt_id: str | None = None) -> list[dict]:
        """List all skill sets.

        Args:
            user_id: User ID for filtering (optional in Phase 1)
            bolt_id: Bot ID, defaults to self.bot_id if not provided

        Returns:
            List of SkillSet dicts
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id
        return self.skill_set_repo.list_all(user_id, bolt_id=effective_bolt_id, engine_type=self.engine_type)

    def update_skill_set(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        is_default: bool | None = None,
        bolt_id: str | None = None
    ) -> dict | None:
        """Update a skill set.

        Args:
            skill_set_id: Skill set ID
            user_id: User ID for permission check
            name: New name (optional)
            description: New description (optional)
            is_default: New default status (optional)
            bolt_id: Bot ID, defaults to self.bot_id if not provided

        Returns:
            Updated SkillSet dict or None

        Raises:
            ValueError: If name contains underscore or is already taken
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id

        skill_set = self.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            return None

        # Cannot update builtin skill sets (except description)
        if skill_set.get('is_builtin') and name is not None:
            raise ValueError("Cannot modify builtin skill set name")

        update_data = {'gmt_modified': datetime.utcnow()}

        if name is not None:
            self._validate_name(name)
            # Check name uniqueness for the same user (排除已删除 Bot 的技能集)
            if hasattr(self.skill_set_repo, 'list_all_exclude_deleted'):
                existing_sets = self.skill_set_repo.list_all_exclude_deleted(user_id, bolt_id=effective_bolt_id, engine_type=self.engine_type)
            else:
                existing_sets = self.skill_set_repo.list_all(user_id, bolt_id=effective_bolt_id, engine_type=self.engine_type)
            for existing in existing_sets:
                if existing.get('name') == name and existing.get('id') != skill_set_id:
                    raise ValueError(f"Skill set name '{name}' already exists for this user")
            update_data['name'] = name

        if description is not None:
            update_data['description'] = description

        if is_default is not None:
            update_data['is_default'] = is_default

        updated = self.skill_set_repo.update(skill_set_id, update_data)

        # Update metadata file
        SkillSetMetadataWriter(skill_set_repo=self.skill_set_repo, skill_repo=self.skill_repo, skills_dir=self.skills_dir, user_id=user_id, bot_id=self.bot_id).write_metadata()

        return updated

    def delete_skill_set(self, skill_set_id: str, user_id: str | None = None) -> bool:
        """Delete a skill set.

        Args:
            skill_set_id: Skill set ID
            user_id: User ID for permission check

        Returns:
            True if deleted, False if not found

        Raises:
            ValueError: If trying to delete a builtin/default/active skill set
        """
        skill_set = self.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            return False

        if skill_set.get('is_builtin'):
            raise ValueError("Cannot delete builtin skill set")

        if skill_set.get('is_default'):
            raise ValueError("默认技能集不允许删除")

        if skill_set.get('is_active'):
            raise ValueError("当前激活的技能集不允许删除，请先切换到其他技能集")

        result = self.skill_set_repo.delete(skill_set_id)

        # Update metadata file
        SkillSetMetadataWriter(skill_set_repo=self.skill_set_repo, skill_repo=self.skill_repo, skills_dir=self.skills_dir, user_id=user_id, bot_id=self.bot_id).write_metadata()

        return result

    async def add_skills_to_set(
        self,
        skill_set_id: str,
        skill_ids: list[str],
        user_id: str | None = None,
        _internal: bool = False,
    ) -> dict[str, Any]:
        """Add skills to a skill set.

        Args:
            skill_set_id: Skill set ID
            skill_ids: List of skill IDs to add (can be database IDs or market skill paths)
            user_id: User ID for permission check
            _internal: If True, bypass the default skill set protection (for init only)

        Returns:
            Dict with 'success' and 'failed' lists
        """

        skill_set = self.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            raise ValueError(f"Skill set '{skill_set_id}' not found")

        # Check if this is the default skill set - users cannot modify it
        if skill_set.get('is_default') and not _internal:
            raise ValueError("默认技能集不允许修改")

        results = {"success": [], "failed": [], "activation_failed": []}

        for skill_id in skill_ids:
            skill = None

            # First, try to find by database ID (if skill_id is numeric)
            try:
                if skill_id.isdigit():
                    skill = self.skill_repo.get_by_id(skill_id)
            except (ValueError, AttributeError):
                pass

            # If not found by ID, try to find by git_path (name)
            if not skill:
                # Try to find skill by git_path in database
                all_skills = self.skill_repo.list_skills(bolt_id=self.bot_id)
                for s in all_skills:
                    git_path = s.get('git_path', '')
                    # Match by full git_path or by name part
                    if git_path.endswith(skill_id) or s.get('name') == skill_id:
                        skill = s
                        break

            # If still not found, try to get from market (git repo) and create
            if not skill:
                # Check if it's a market skill (from git repo)
                # Try to find in all market skills
                market_skill = None
                all_market_skills = self.skill_service.get_skills_in_path("")
                for ms in all_market_skills:
                    if ms['id'] == skill_id or ms.get('path', '').endswith(skill_id):
                        market_skill = ms
                        break

                if market_skill:
                    # Create skill record from market data
                    skill = self._create_skill_from_market(market_skill, user_id, self.bot_id)
                else:
                    results["failed"].append({"skill_id": skill_id, "error": "Skill not found"})
                    continue

            # Local skills are stored in a per-Bot workspace and must never be
            # associated with another Bot's skill set. Numeric skill IDs are
            # globally resolvable, so a stale caller bot_id could otherwise
            # create a cross-Bot association here.
            skill_git_path = skill.get("git_path", "")
            target_bot_id = skill_set.get("bolt_id") or self.bot_id
            if (
                skill_git_path.startswith("local://")
                and skill.get("bolt_id") is not None
                and skill.get("bolt_id") != target_bot_id
            ):
                results["failed"].append(
                    {
                        "skill_id": skill_id,
                        "error": "Skill belongs to another bot",
                    }
                )
                logger.warning(
                    "[add_skills_to_set] Rejected cross-Bot local skill: "
                    "skill_id=%s, skill_bot_id=%s, skill_set_id=%s, "
                    "skill_set_bot_id=%s",
                    skill_id,
                    skill.get("bolt_id"),
                    skill_set_id,
                    target_bot_id,
                )
                continue

            # Check if already associated in the same skill set
            existing_skills = self.skill_set_repo.get_skills_in_set(skill_set_id)
            already_exists = any(s.get('id') == skill.get('id') for s in existing_skills)
            if already_exists:
                results["failed"].append({"skill_id": skill_id, "error": "Already in skill set"})
                continue

            # Check if skill exists in ANY OTHER skill set of the same bot
            # (one skill can only belong to one skill set per bot)
            all_sets = self.list_skill_sets(user_id=user_id, bolt_id=self.bot_id)
            for other_set in all_sets:
                if str(other_set.get('id')) == skill_set_id:
                    continue  # Skip target set
                other_set_skills = self.get_set_skills(str(other_set.get('id')), user_id=user_id)
                if any(s.get('id') == skill.get('id') for s in other_set_skills):
                    skill_name = skill.get('name', skill_id)
                    results["failed"].append({
                        "skill_id": skill_id,
                        "error": f"Skill '{skill_name}' already exists in another skill set for this bot",
                    })
                    break  # Don't continue to add_skill_to_set

            # Skip if already failed due to cross-set conflict
            if any(f.get('skill_id') == skill_id for f in results["failed"]):
                continue

            # Create association using repository
            if not self.skill_set_repo.add_skill_to_set(
                skill_set_id, skill.get('id')
            ):
                results["failed"].append(
                    {"skill_id": skill_id, "error": "Failed to add skill to skill set"}
                )
                continue
            results["success"].append({"skill_id": skill.get('id'), "name": skill.get('name')})

        # Update metadata file
        SkillSetMetadataWriter(skill_set_repo=self.skill_set_repo, skill_repo=self.skill_repo, skills_dir=self.skills_dir, user_id=user_id, bot_id=self.bot_id).write_metadata()

        # Auto-activate skills if this skill set is currently active
        if skill_set.get('is_active') and results["success"]:
            logger.info(f"[add_skills_to_set] Skill set {skill_set_id} is active, auto-activating {len(results['success'])} skills")
            # Collect git_paths for all skills to activate concurrently
            git_paths = []
            skill_infos = []  # (skill_id, skill_name, git_path)
            for success_item in results["success"]:
                skill_id = success_item["skill_id"]
                skill = self.skill_repo.get_by_id(skill_id)
                if skill and skill.get('git_path'):
                    git_paths.append(skill.get('git_path'))
                    skill_infos.append((skill_id, skill.get('name'), skill.get('git_path')))

            if git_paths:
                # 修复: 传递 user_id 和 bolt_id 到 activate_skill，确保设备操作正确路由
                # 获取 Bot 的 owner_id（用于设备绑定查询）
                bot = self._bot_repo.get_by_id(self.bot_id)
                owner_id = bot.get("owner_id") if bot else None
                # fallback: 如果获取不到 owner_id，使用 entity_id 或 user_id
                if not owner_id:
                    owner_id = self.entity_id or user_id

                # Activate all concurrently with proper device routing
                activation_results = await asyncio.gather(
                    *[
                        self.skill_service.activate_skill(
                            gp,
                            user_id=owner_id,
                            bolt_id=self.bot_id,
                        )
                        for gp in git_paths
                    ],
                    return_exceptions=True
                )
                # 收集激活失败的信息，让调用方可见
                activation_failed = []
                for (skill_id, skill_name, git_path), result in zip(skill_infos, activation_results):
                    if isinstance(result, Exception):
                        logger.warning(f"[add_skills_to_set] Failed to auto-activate skill {skill_name}: {result}")
                        activation_failed.append({
                            "skill_id": skill_id,
                            "name": skill_name,
                            "reason": str(result)
                        })
                    elif result is not True:
                        logger.warning(
                            "[add_skills_to_set] Auto-activation source is "
                            "unavailable for skill %s",
                            skill_name,
                        )
                        activation_failed.append({
                            "skill_id": skill_id,
                            "name": skill_name,
                            "reason": "activation source is unavailable",
                        })
                    else:
                        logger.debug(f"[add_skills_to_set] Auto-activated skill {skill_name}: {result}")

                # 将 activation_failed 信息写入返回结果
                if activation_failed:
                    results["activation_failed"] = activation_failed
                    logger.warning(f"[add_skills_to_set] {len(activation_failed)} skills failed to auto-activate")

            # Do not publish a mapping set containing a missing source. A
            # failed local activation is intentionally fail-closed so the
            # runtime never receives a dangling active link.
            if not results["activation_failed"]:
                self._sync_symlinks_to_device_if_needed(user_id)

        return results

    def _create_skill_from_market(self, market_skill: dict[str, Any], user_id: str | None = None, bolt_id: str | None = None):
        """Create a skill record from market (git repo) data."""
        effective_bolt_id = bolt_id if bolt_id else self.bot_id

        # Get full path from market skill and convert to relative path
        full_path = market_skill.get('full_path', '')
        if full_path:
            # Extract relative path from skills-repo directory
            # Use _get_market_repo_dir() which returns global_repo_dir in cloud mode
            # This matches the path used in _convert_skill_info_to_dict for full_path generation
            market_repo_dir = str(self.skill_service._get_market_repo_dir())
            if full_path.startswith(market_repo_dir):
                rel_path = full_path[len(market_repo_dir):].lstrip('/')
                git_path = f"git://{rel_path}"
            else:
                # Fallback to using link_name which contains the full relative path
                # link_name format: category_subcategory_skill-id -> category/subcategory/skill-id
                link_name = market_skill.get('link_name', '')
                if link_name and '_' in link_name:
                    # link_name format: category_subcategory_skill-id
                    parts = link_name.split('_', 2)
                    if len(parts) >= 3:
                        rel_path = f"{parts[0]}/{parts[1]}/{market_skill['id']}"
                    else:
                        rel_path = market_skill['id']
                else:
                    rel_path = market_skill['id']
                git_path = f"git://{rel_path}"
        else:
            # Fallback to short id
            git_path = f"git://{market_skill['id']}"

        # Check if skill already exists by path
        existing_skills = self.skill_repo.list_skills(bolt_id=effective_bolt_id)
        for existing in existing_skills:
            if existing.get('git_path') == git_path:
                return existing

        # Create new skill using repository
        # Note: id is auto-increment, don't specify it
        skill_data = {
            'name': market_skill['name'],
            'description': market_skill.get('description', ''),
            'git_path': git_path,
            'category': market_skill.get('category', 'general'),
            'tags': '[]',
            'input_schema': '',
            'output_schema': '',
            'is_public': True,
            'is_builtin': False,
            'user_id': user_id,
            'bolt_id': effective_bolt_id,
            'gmt_created': datetime.utcnow(),
            'gmt_modified': datetime.utcnow()
        }
        return self.skill_repo.create(skill_data)

    async def remove_skill_from_set(
        self,
        skill_set_id: str,
        skill_id: str,
        user_id: str | None = None
    ) -> bool:
        """Remove a skill from a skill set.

        Args:
            skill_set_id: Skill set ID
            skill_id: Skill ID to remove
            user_id: User ID for permission check

        Returns:
            True if removed, False if not found
        """
        # Check if this is the default skill set
        skill_set = self.get_skill_set(skill_set_id, user_id)
        if skill_set and skill_set.get('is_default'):
            # Default skill set: write exclusion record instead of deleting
            if not user_id:
                user_id = self.entity_id
            self.skill_set_repo.add_default_skill_exclusion(
                user_id=user_id,
                bot_id=self.bot_id,
                skill_set_id=int(skill_set_id),
                skill_id=int(skill_id),
            )
            logger.info(
                f"[remove_skill_from_set] Excluded skill {skill_id} from default set "
                f"{skill_set_id} for user={user_id}, bot={self.bot_id}"
            )

            # Deactivate the skill symlink (skill is not shared across sets,
            # so no need to check other sets for references)
            skill = self.skill_repo.get_by_id(skill_id)
            if skill and skill.get('git_path'):
                git_path = skill['git_path']
                if git_path.startswith("git://"):
                    rel_path = git_path[6:]
                    link_name = self.skill_service.get_link_name(rel_path)
                elif git_path.startswith("local://"):
                    rel_path = git_path[8:]
                    link_name = rel_path.rstrip('/').split('/')[-1]
                else:
                    rel_path = git_path
                    link_name = self.skill_service.get_link_name(rel_path)
                try:
                    await self.skill_service.deactivate_skill(
                        link_name, bolt_id=self.bot_id, user_id=user_id
                    )
                except Exception as e:
                    logger.error(
                        "[remove_skill_from_set] Failed to deactivate skill %s: %s",
                        link_name, e,
                    )
                    # Rollback: remove the exclusion record
                    self.skill_set_repo.remove_default_skill_exclusion(
                        user_id=user_id,
                        bot_id=self.bot_id,
                        skill_set_id=int(skill_set_id),
                        skill_id=int(skill_id),
                    )
                    return False

            # Sync symlinks to device
            self._sync_symlinks_to_device_if_needed(user_id)
            return True

        # 在入口处用 skill_set 的真实 bolt_id 覆盖 self.bot_id
        # 前端调用时通常不传 bot_id，服务端 fallback 到 "default"，但被删的 skill_set
        # 可能绑在其他 bot 上。覆盖后，后续所有用 self.bot_id 的地方
        # （SkillSetMetadataWriter、_sync_symlinks_to_device_if_needed、
        #  get_symlink_mappings 等）均自动使用正确值，无需逐一修改
        if skill_set and skill_set.get('bolt_id'):
            logger.info(
                f"[remove_skill_from_set] overriding bot_id: {self.bot_id} -> "
                f"{skill_set['bolt_id']} (from skill_set_id={skill_set_id})"
            )
            self.bot_id = skill_set['bolt_id']

        # Get skill info before deleting (needed for deactivation)
        skill = self.skill_repo.get_by_id(skill_id)
        skill_name = skill.get('name') if skill else skill_id
        skill_git_path = skill.get('git_path') if skill else None
        logger.debug(f"[remove_skill_from_set] Skill found: {skill_name}, git_path: {skill_git_path}")

        result = self.skill_set_repo.remove_skill_from_set(skill_set_id, skill_id)

        if not result:
            return False

        # Update metadata file
        logger.info(f"[remove_skill_from_set] Updating metadata file after removing skill {skill_name}")
        try:
            SkillSetMetadataWriter(skill_set_repo=self.skill_set_repo, skill_repo=self.skill_repo, skills_dir=self.skills_dir, user_id=user_id, bot_id=self.bot_id).write_metadata()
            logger.info("[remove_skill_from_set] Metadata file updated successfully")
        except Exception as e:
            logger.error(f"[remove_skill_from_set] Failed to update metadata: {e}")

        # 查当前 bot_id 下所有激活集，集合判断替换 LIMIT 1 单值比较
        current_active_ids = {
            str(s.get('id'))
            for s in self.skill_set_repo.get_all_active_skill_sets(
                user_id=self.entity_id, bolt_id=self.bot_id, engine_type=self.engine_type
            )
        }
        logger.info(
            f"[remove_skill_from_set] active skill sets for user_id={self.entity_id}, "
            f"bot_id={self.bot_id}: {current_active_ids}"
        )
        if skill_set_id in current_active_ids:
            logger.info(f"[remove_skill_from_set] Skill set {skill_set_id} is active, auto-deactivating skill {skill_name}")
            try:
                # Use git_path to get the correct link name for deactivation
                if skill_git_path:
                    # Extract relative path from git_path (e.g., "git://business/content/skill" -> "business/content/skill")
                    if skill_git_path.startswith("git://"):
                        rel_path = skill_git_path[6:]
                    elif skill_git_path.startswith("local://"):
                        rel_path = skill_git_path[8:]
                    else:
                        rel_path = skill_git_path
                    # 软链名口径必须与 get_symlink_mappings:1079 一致 (取末段),
                    # 否则宿主 skills/ 下找不到 target 文件名 → C016 残留。
                    # 历史上这里用 get_link_name(全路径下划线),但宿主目录里实际
                    # 名字是末段;DB 表 ac_skill.link_name 仍存全路径下划线 (不影响 fs 删链)。
                    link_name = rel_path.rstrip('/').split('/')[-1]
                    logger.debug(f"[remove_skill_from_set] Deactivating with link name: {link_name}")
                    deactivate_success = await self.skill_service.deactivate_skill(link_name, bolt_id=self.bot_id, user_id=user_id)
                else:
                    # Fallback to skill_id if no git_path
                    deactivate_success = await self.skill_service.deactivate_skill(skill_id, bolt_id=self.bot_id, user_id=user_id)
                logger.info(f"[remove_skill_from_set] Auto-deactivated skill {skill_name}: {deactivate_success}")
            except Exception as e:
                logger.warning(f"[remove_skill_from_set] Failed to auto-deactivate skill {skill_name}: {e}")

            # 关键修复：同步软链到设备（即使为空也要同步，确保设备端清空软链）
            self._sync_symlinks_to_device_if_needed(user_id)

        return True

    def get_set_skills(
        self,
        skill_set_id: str,
        user_id: str | None = None
    ) -> list[dict]:
        """Get all skills in a skill set.

        For default skill sets, filters out user-excluded skills from
        ac_default_skillset_skill_exclusion.

        Args:
            skill_set_id: Skill set ID
            user_id: User ID for permission check and exclusion lookup

        Returns:
            List of Skill dicts
        """
        skill_set = self.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            return []

        skills = self.skill_set_repo.get_skills_in_set(skill_set_id)

        # Default skill set: filter out user-excluded skills
        # (mirrors get_set_mcp_servers which filters ac_default_skillset_mcp_exclusion)
        if skill_set.get('is_default') and user_id and self.bot_id:
            excluded = self.skill_set_repo.get_excluded_skills(
                user_id=user_id, bot_id=self.bot_id, skill_set_id=int(skill_set_id)
            )
            excluded_ids = set(excluded)
            if excluded_ids:
                # skill id from _skill_to_dict is str, excluded_ids from DB are int
                skills = [s for s in skills if int(s.get('id', 0)) not in excluded_ids]

        return skills

    def ensure_default_skill_set(self, user_id: str | None = None, bolt_id: str | None = None) -> dict:
        """Ensure default skill set exists.

        Args:
            user_id: User ID (optional in Phase 1)
            bolt_id: Bot ID，为空时默认使用 self.bot_id

        Returns:
            Default SkillSet dict
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id

        # Look for existing default
        default = self.skill_set_repo.get_default(user_id, bolt_id=effective_bolt_id, engine_type=self.engine_type)

        if default:
            return default

        # Create default skill set
        return self.create_skill_set(
            name="默认技能集",
            description="系统默认技能集，用户可以根据需要添加或移除技能",
            user_id=user_id,
            is_default=True,
            bolt_id=effective_bolt_id
        )

    def get_default_skill_set(self, user_id: str | None = None, bolt_id: str | None = None) -> dict | None:
        """Get default skill set.

        Args:
            user_id: User ID (optional in Phase 1)
            bolt_id: Bot ID，为空时默认使用 self.bot_id

        Returns:
            Default SkillSet dict or None
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id
        return self.skill_set_repo.get_default(user_id, bolt_id=effective_bolt_id, engine_type=self.engine_type)

    def get_all_skill_sets_with_skills(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None
    ) -> list[dict[str, Any]]:
        """获取所有能力集（包括激活和非激活），每个能力集包含其技能列表

        Args:
            user_id: User ID for filtering (optional)
            bolt_id: Bot ID，为空时默认使用 self.bot_id

        Returns:
            List[Dict]: 能力集列表，每个能力集包含 skills 字段
            [
                {
                    "id": str,
                    "name": str,
                    "description": str,
                    "is_default": bool,
                    "is_builtin": bool,
                    "is_active": bool,
                    "user_id": str,
                    "bolt_id": str,
                    "env": str,
                    "gmt_created": str,
                    "gmt_modified": str,
                    "skills": [                 # 该能力集包含的技能列表
                        {
                            "id": str,
                            "name": str,
                            "description": str,
                            "git_path": str,
                            "category": str,
                            "tags": list,
                            ...
                        }
                    ],
                    "skill_count": int
                }
            ]
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id
        effective_user_id = user_id if user_id else self.user_id or self.entity_id

        logger.info(f"[get_all_skill_sets_with_skills] user_id={effective_user_id}, bolt_id={effective_bolt_id}")

        # 1. 获取所有能力集（激活和非激活）
        skill_sets = self.skill_set_repo.list_all(
            user_id=effective_user_id,
            bolt_id=effective_bolt_id,
            engine_type=self.engine_type
        )

        logger.info(f"[get_all_skill_sets_with_skills] 找到 {len(skill_sets)} 个能力集")

        # 2. 为每个能力集查询技能
        for skill_set in skill_sets:
            skill_set_id = skill_set.get('id')
            skills = self.skill_set_repo.get_skills_in_set(str(skill_set_id))
            skill_set['skills'] = skills
            skill_set['skill_count'] = len(skills)
            logger.debug(f"[get_all_skill_sets_with_skills] 能力集 {skill_set.get('name')} 包含 {len(skills)} 个技能")

        return skill_sets

    def get_all_skill_sets_with_mcps(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None
    ) -> list[dict[str, Any]]:
        """获取所有能力集（包括激活和非激活）及其关联的 MCP 列表

        Args:
            user_id: User ID for filtering (optional)
            bolt_id: Bot ID，为空时默认使用 self.bot_id

        Returns:
            List[Dict]: 能力集列表，每个能力集包含 mcps 字段
            [
                {
                    "id": str,
                    "name": str,
                    "description": str,
                    "is_default": bool,
                    "is_builtin": bool,
                    "is_active": bool,
                    "user_id": str,
                    "bolt_id": str,
                    "bot_id": str,          # 同 bolt_id，用于前端兼容
                    "env": str,
                    "gmt_created": str,
                    "gmt_modified": str,
                    "mcps": [               # 该能力集关联的 MCP 列表
                        {
                            "id": str,
                            "server_code": str,
                            "name": str,
                            "description": str,
                            "icon": str,
                            "status": str
                        }
                    ],
                    "mcp_count": int        # MCP 数量
                }
            ]
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id
        effective_user_id = user_id if user_id else self.entity_id

        logger.info(f"[get_all_skill_sets_with_mcps] user_id={effective_user_id}, bolt_id={effective_bolt_id}")

        # 1. 获取所有能力集
        skill_sets = self.skill_set_repo.list_all(
            user_id=effective_user_id,
            bolt_id=effective_bolt_id,
            engine_type=self.engine_type
        )

        # 排序：默认能力集在前，然后按创建时间排序
        skill_sets = sorted(skill_sets, key=lambda s: (not s.get('is_default'), s.get('gmt_created')))

        logger.info(f"[get_all_skill_sets_with_mcps] 找到 {len(skill_sets)} 个能力集")

        # 2. 为每个能力集查询 MCP
        for skill_set in skill_sets:
            skill_set_id = skill_set.get('id')
            mcps = self.get_set_mcp_servers(str(skill_set_id), effective_user_id, effective_bolt_id)
            skill_set['mcps'] = mcps
            skill_set['mcp_count'] = len(mcps)
            # 添加 bot_id 字段（前端兼容）
            skill_set['bot_id'] = skill_set.get('bolt_id') or 'default'
            logger.debug(f"[get_all_skill_sets_with_mcps] 能力集 {skill_set.get('name')} 包含 {len(mcps)} 个 MCP")

        return skill_sets

    def get_active_skills(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
    ) -> list[dict]:
        """The bot's active, de-duped skill DB records.

        Each record carries ``git_path`` — the source of truth for the skill's actual
        location (``git://<repo-rel>`` for shared market skills, ``local://<abs host
        path>`` for user uploads). Collects skills across all active skill sets
        (including the per-engine default set), drops default-set user exclusions, and
        de-dups by ``git_path``.

        Used by both ``get_symlink_mappings`` (ARCA container symlinks) and the
        config-compose collector (teclaw — which reads ``git_path`` directly, no
        container round-trip).
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id
        effective_user_id = user_id if user_id else self.entity_id

        # 1. 查询所有激活的技能集（包含默认能力集）
        active_skill_sets = self.skill_set_repo.get_all_active_skill_sets(
            user_id=effective_user_id,
            bolt_id=effective_bolt_id,
            engine_type=self.engine_type
        )
        if not active_skill_sets:
            logger.warning(f"[get_active_skills] 未找到激活的技能集: user_id={effective_user_id}, bolt_id={effective_bolt_id}")
            return []

        # 2. 遍历每个能力集，收集所有技能
        all_skills = []
        for skill_set in active_skill_sets:
            skill_set_id = skill_set.get('id')
            skills = self.skill_set_repo.get_skills_in_set(str(skill_set_id))

            # Default skill set: filter out user-excluded skills
            # (mirrors get_set_mcp_servers which filters ac_default_skillset_mcp_exclusion)
            if skill_set.get('is_default') and effective_user_id and effective_bolt_id:
                excluded = self.skill_set_repo.get_excluded_skills(
                    user_id=effective_user_id,
                    bot_id=effective_bolt_id,
                    skill_set_id=int(skill_set_id),
                )
                excluded_ids = set(excluded)
                if excluded_ids:
                    # skill id from _skill_to_dict is str, excluded_ids from DB are int
                    skills = [s for s in skills if int(s.get('id', 0)) not in excluded_ids]
            all_skills.extend(skills)

        # 3. 根据 git_path 去重
        seen_git_paths = set()
        unique_skills = []
        for skill in all_skills:
            git_path = skill.get('git_path', '')
            if git_path and git_path not in seen_git_paths:
                seen_git_paths.add(git_path)
                unique_skills.append(skill)
        return unique_skills

    # ====== Symlink Activation Config ======

    def get_symlink_mappings(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        additional_skill_paths: list[str] | None = None,
    ) -> list[SynlinkMappingInfo]:
        """生成技能激活软链配置（支持多能力集激活）

        用于申请设备时传入 ARCA 沙箱，运行时通过 OSS 挂载看到软链。
        支持多个能力集同时激活，合并软链配置并去重。

        Args:
            user_id: 用户 ID
            bolt_id: Bot ID，默认使用 self.bot_id
            additional_skill_paths: 当前请求直接激活、但尚未属于 active
                SkillSet 的 Skill locator。它们与 SkillSet 快照使用同一套
                Engine/Planner 路径规则生成一次完整 publish。

        Returns:
            List[SynlinkMappingInfo]: 软链配置列表（已去重）
        """
        unique_skills = self.get_active_skills(user_id=user_id, bolt_id=bolt_id)

        # Direct Skill CRUD is intentionally orthogonal to SkillSet membership.
        # The device boundary accepts a complete mapping publish, so the current
        # request must be merged into the active-SkillSet snapshot explicitly;
        # otherwise bindpath can report success while never seeing the requested
        # Skill. Keep the locator as the identity and let the common mapping code
        # below select Legacy/Pool roots for every filesystem engine.
        seen_paths = {
            str(skill.get("git_path", ""))
            for skill in unique_skills
            if skill.get("git_path")
        }
        requested_paths = additional_skill_paths or []
        for skill_path in requested_paths:
            if not isinstance(skill_path, str) or not skill_path.startswith(
                ("git://", "local://")
            ):
                raise ValueError(
                    f"Unsupported direct Skill locator: {skill_path!r}"
                )
            if skill_path in seen_paths:
                continue
            unique_skills.append({"name": "", "git_path": skill_path})
            seen_paths.add(skill_path)

        if not unique_skills:
            return []

        logger.info(f"[get_symlink_mappings] 总技能数量（去重后）: {len(unique_skills)}")

        # 4. 构造基础路径（设备容器内的绝对路径）
        # 不同引擎使用不同的容器内技能目录
        ENGINE_SKILLS_DIR_MAP = {
            "claude_code": "/home/admin/.claude/skills",
            "aicoding": "/home/admin/.claude/skills",
            "openclaw": "/home/admin/.openclaw/workspace/skills",
            "moltis": "/home/admin/.moltis/skills",
            "hermes": "/home/admin/.hermes/skills",
        }
        # aicoding 引擎的 skills-repo 实际存储位置不同
        ENGINE_SKILLS_REPO_DIR_MAP = {
            "aicoding": "/home/admin/.aicoding/skills-repo",
            "hermes": "/home/admin/.hermes/skills-repo",
        }
        base_skills_dir = Path(
            ENGINE_SKILLS_DIR_MAP.get(self.engine_type, "/home/admin/.openclaw/workspace/skills")
        )
        # aicoding 引擎使用独立的 skills-repo 目录
        skills_repo_dir = Path(
            ENGINE_SKILLS_REPO_DIR_MAP.get(self.engine_type, str(base_skills_dir / "skills-repo"))
        )
        pool_layout_paths = None
        pool_owner_id = self.user_id or self.entity_id
        if pool_owner_id is not None:
            pool_layout_paths = self._pool_layout_paths(
                str(pool_owner_id),
                str(self.bot_id),
                self.engine_type,
            )
        if pool_layout_paths is not None:
            active_path, local_path, repo_path = pool_layout_paths
            base_skills_dir = Path(active_path)
            skills_repo_dir = Path(repo_path)

        # singlebox 本机模式: engine adapter 跑在宿主 macOS,容器视图 /home/admin/...
        # 不存在。把容器路径前缀替换成宿主 per-bot workspace 路径
        # (bolt_data/staff_X/<bot>/openclaw/workspace/skills),让 engine 写到 per-bot
        # 物理目录,避免多 bot 共宿主时撞共享根 ~/.openclaw/workspace/skills 的 bug。
        # 线上 Arca 容器内 /home/admin/... 真实存在,不需要换。
        #
        # 注:绕过 path_factory.get_bot_skills_dir 的 LOCAL 短路 (它在 singlebox 模式
        # 下返回共享根,会破坏 per-bot 隔离),直接用 get_bot_engine_dir 算 per-bot path。
        # is_desktop bot 不走这条 — desktop bot 的 mapping 仍按容器视角下发,引擎在
        # VM 内自己解析。
        from agentclaw.community.utils.env_utils import is_local_mode
        if is_local_mode() and not self.is_desktop:
            from agentclaw.community.core.workspace.path_factory import get_bot_engine_dir
            per_bot_skills_root = (
                get_bot_engine_dir(
                    self.entity_id or "default",
                    self.bot_id,
                    self.engine_type,
                    self.entity_type or "staff",
                )
                / "workspace"
                / "skills"
            )
            container_root = Path("/home/admin/.openclaw/workspace/skills")
            if base_skills_dir.is_relative_to(container_root):
                base_skills_dir = per_bot_skills_root / base_skills_dir.relative_to(container_root)
            if skills_repo_dir.is_relative_to(container_root):
                skills_repo_dir = per_bot_skills_root / skills_repo_dir.relative_to(container_root)
            logger.info(
                "[get_symlink_mappings] LOCAL+non-desktop → per-bot paths: "
                "entity=%s bot=%s engine=%s base_skills_dir=%s skills_repo_dir=%s",
                self.entity_id, self.bot_id, self.engine_type, base_skills_dir, skills_repo_dir,
            )

        skills_local_dir = base_skills_dir / "skills-local"
        if pool_layout_paths is not None:
            skills_local_dir = Path(local_path)

        # 5. 解析 git_path 生成 SynlinkMappingInfo 列表（使用绝对路径）
        symlinks = []
        for skill in unique_skills:
            git_path = skill.get('git_path', '')
            skill_name = skill.get('name', '')

            logger.debug(f"[get_symlink_mappings] 处理技能: name={skill_name}, git_path={git_path}")

            if git_path.startswith('git://'):
                # Git 技能: 指向 skills-repo
                rel_path = git_path[6:]
                link_name = rel_path.split('/')[-1] if '/' in rel_path else rel_path
                source = str(skills_repo_dir / rel_path)
                target = str(base_skills_dir / link_name)
                symlinks.append(SynlinkMappingInfo(source=source, target=target))

            elif git_path.startswith('local://'):
                # local:// 可能是绝对路径 local:///aidesktop/.../skills-local/skill-name
                # 或者相对名称 local://skill-name
                path_part = git_path[8:]
                # 从路径中提取技能名称（最后一个目录名）
                if path_part.startswith('/'):
                    # 绝对路径格式: /aidesktop/.../skills-local/skill-name
                    link_name = skill_name or path_part.rstrip('/').split('/')[-1]
                    source = (
                        canonical_pool_local_path(path_part, skills_local_dir)
                        if pool_layout_paths is not None
                        else path_part.rstrip('/')
                    )
                else:
                    # 相对名称格式: skill-name
                    source_name = path_part.split('/')[-1] if '/' in path_part else path_part
                    # Replacement packages are staged under a temporary
                    # directory (for example ``.foo.replacement-*``), but
                    # their runtime link must retain the logical Skill name.
                    link_name = skill_name or source_name
                    source = str(skills_local_dir / source_name)
                target = str(base_skills_dir / link_name)
                symlinks.append(SynlinkMappingInfo(source=source, target=target))

        resolved_mappings = symlinks
        if requested_paths:
            # A single runtime link name cannot safely resolve to two corpora.
            # Reject collisions before calling the device boundary; identical
            # mappings are harmless duplicates and collapse in insertion order.
            resolved_mappings = []
            mappings_by_target: dict[str, SynlinkMappingInfo] = {}
            for mapping in symlinks:
                existing = mappings_by_target.get(mapping.target)
                if existing is None:
                    mappings_by_target[mapping.target] = mapping
                    resolved_mappings.append(mapping)
                    continue
                if existing.source != mapping.source:
                    raise ValueError(
                        "Conflicting Skill mappings for runtime target "
                        f"{mapping.target!r}: {existing.source!r} != "
                        f"{mapping.source!r}"
                    )

        logger.info(f"[get_symlink_mappings] 生成软链配置完成: symlinks_count={len(resolved_mappings)}")
        for sm in resolved_mappings:
            logger.info(f"[get_symlink_mappings] symlink: source={sm.source}, target={sm.target}")

        return resolved_mappings

    # ====== MCP Server Management in SkillSet ======

    async def add_mcp_to_skill_set(
        self,
        skill_set_id: str,
        server_code: str,
        user_id: str
    ) -> dict[str, Any]:
        """Add MCP server to a skill set.

        Args:
            skill_set_id: Skill set ID
            server_code: MCP server code
            user_id: User ID (required, cannot be empty)

        This operation performs a blocking sync to the device:
        1. Validates the user has permission to use the MCP server
        2. Adds to database (maintains association, no API key handling)
        3. Syncs the MCP basic info to device (blocking)

        Note: API key configuration is handled separately via config update interface.
        If device sync fails, the MCP is removed from database and the operation fails.
        """
        if not user_id:
            raise ValueError("user_id is required and cannot be empty")

        skill_set = self.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            raise ValueError(f"Skill set '{skill_set_id}' not found")

        if skill_set.get('is_default'):
            raise ValueError("默认技能集不允许修改")

        # Get MCP server from MCP Center
        mcp_data = self.mcp_center.get_mcp_detail(server_code)
        if not mcp_data:
            return {"success": False, "error": f"MCP server '{server_code}' not found"}

        mcp_name = mcp_data.get("name", server_code)
        mcp_description = mcp_data.get("description")
        mcp_icon = mcp_data.get("icon")

        # Check if already associated (by server_code)
        existing = self.skill_set_repo.get_mcp_servers_in_set(skill_set_id)
        if any(str(s.get('server_code')) == server_code for s in existing):
            return {"success": False, "error": "MCP server already in skill set"}

        # Create association (store server_code, name, description and icon)
        from agentclaw.community.utils.env_utils import get_current_env
        current_env = get_current_env()
        if not self.skill_set_repo.add_mcp_to_set(
            skill_set_id,
            server_code,
            mcp_name,
            mcp_description,
            mcp_icon,
            user_id,
            env=current_env,
        ):
            return {
                "success": False,
                "error": "Failed to add MCP server to skill set",
                "server_code": server_code,
            }

        # Sync to device (blocking - must succeed for operation to be considered successful)
        # Note: API key is NOT passed during initial add, user should configure it separately
        push_result = await self._mcp_sync_service.sync_mcp_detail(
            user_id=user_id,
            mcp_data=mcp_data,
            bot_id=self.bot_id,
            entity_id=self.entity_id,
        )
        if not push_result.get("success"):
            error = push_result.get("error", "Unknown error")
            logger.error(f"[add_mcp_to_skill_set] Device sync failed: {error}")
            self.skill_set_repo.remove_mcp_from_set(skill_set_id, server_code)
            return {
                "success": False,
                "error": f"Failed to sync MCP to device: {error}",
                "server_code": server_code,
                "sync_error": error,
            }

        # 触发 scope 同步（更新 allowServers 列表 + passport）
        scope_result = await self.refresh_mcp_scope(user_id=user_id)
        if not scope_result.get("success"):
            error = scope_result.get("error", "Unknown error")
            logger.error("[add_mcp_to_skill_set] 刷新 MCP 授权范围失败: %s", error)
            return {
                "success": False,
                "error": f"MCP 已添加，但授权范围刷新失败: {error}",
                "server_code": server_code,
                "sync_error": error,
            }

        logger.info(f"[add_mcp_to_skill_set] user_id={user_id}, bot_id={self.bot_id}, Sync triggered after adding MCP {server_code}")

        return {
            "success": True,
            "server_code": server_code,
        }

    async def remove_mcp_from_skill_set(
        self,
        skill_set_id: str,
        server_code: str,
        user_id: str | None = None
    ) -> dict[str, Any]:
        """Remove MCP server from a skill set.

        For default skill sets, writes to ac_default_skillset_mcp_exclusion table
        instead of deleting, to support per-user isolation.

        Returns:
            ``{"success": bool, "error": str|None}`` 格式的结果字典。
        """
        skill_set = self.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            return {"success": False, "error": "MCP server not found in skill set"}

        # 判断是否是默认能力集
        is_default = skill_set.get('is_default', False)

        if is_default:
            # 默认能力集：写入排除表（用户隔离）
            if not user_id:
                user_id = self.entity_id
            self.skill_set_repo.add_default_mcp_exclusion(
                user_id=user_id,
                bot_id=self.bot_id,
                skill_set_id=int(skill_set_id),
                server_code=server_code
            )

            # 检查是否仍需从设备删除
            should_remove = True
            try:
                for ss in self.list_skill_sets(user_id=user_id, bolt_id=self.bot_id):
                    if str(ss.get('id')) == skill_set_id:
                        continue
                    if server_code in {m.get('server_code') for m in self.get_set_mcp_servers(str(ss.get('id')), user_id, self.bot_id) if m.get('server_code')}:
                        should_remove = False
                        break
            except Exception as e:
                should_remove = False
                logger.warning(f"[remove_mcp_from_skill_set] Check usage failed: {e}, skip device removal")

            if should_remove:
                remove_result = await self._mcp_sync_service.remove_mcp_detail(
                    server_code=server_code,
                    bot_id=self.bot_id,
                    user_id=self.entity_id,
                )
                if not remove_result.get("success"):
                    err = remove_result.get("error", "Unknown error")
                    logger.error("[remove_mcp_from_skill_set] 设备端移除 MCP 失败: %s", err)
                    # 回滚 exclusion
                    self.skill_set_repo.remove_default_mcp_exclusion(
                        user_id=user_id, bot_id=self.bot_id,
                        skill_set_id=int(skill_set_id), server_code=server_code,
                    )
                    return {"success": False, "error": f"Device removal failed: {err}"}

            # 触发 scope 同步（scope 失败不回滚，设备端已移除即可）
            if user_id:
                scope_result = await self.refresh_mcp_scope(user_id=user_id)
                if not scope_result.get("success"):
                    err = scope_result.get("error", "Unknown error")
                    logger.error("[remove_mcp_from_skill_set] 刷新 MCP 授权范围失败: %s", err)
                    return {"success": False, "error": f"Scope sync failed: {err}"}

            return {"success": True, "error": None}
        else:
            # 普通能力集：删表记录前先保留字段，用于回滚
            existing_mcps = self.skill_set_repo.get_mcp_servers_in_set(skill_set_id)
            mcp_record = next(
                (
                    m
                    for m in existing_mcps
                    if str(m.get("server_code")) == server_code
                ),
                None,
            )
            removed = self.skill_set_repo.remove_mcp_from_set(skill_set_id, server_code)
            if not removed:
                return {"success": False, "error": "MCP server not found in skill set"}

            # 检查是否仍需从设备删除
            should_remove = True
            try:
                for ss in self.list_skill_sets(user_id=user_id, bolt_id=self.bot_id):
                    if str(ss.get('id')) == skill_set_id:
                        continue
                    if server_code in {m.get('server_code') for m in self.get_set_mcp_servers(str(ss.get('id')), user_id, self.bot_id) if m.get('server_code')}:
                        should_remove = False
                        break
                if should_remove and server_code in {c.get("server_code") for c in get_default_mcp_servers(self.engine_type)}:
                    should_remove = False
            except Exception as e:
                should_remove = False
                logger.warning(f"[remove_mcp_from_skill_set] Check usage failed: {e}, skip device removal")

            if should_remove:
                remove_result = await self._mcp_sync_service.remove_mcp_detail(
                    server_code=server_code,
                    bot_id=self.bot_id,
                    user_id=self.entity_id,
                )
                if not remove_result.get("success"):
                    err = remove_result.get("error", "Unknown error")
                    logger.error("[remove_mcp_from_skill_set] 设备端移除 MCP 失败: %s", err)
                    # 回滚 DB 记录
                    if mcp_record:
                        self.skill_set_repo.add_mcp_to_set(
                            skill_set_id, server_code,
                            name=mcp_record.get("name", server_code),
                            description=mcp_record.get("description"),
                            icon=mcp_record.get("icon"),
                            user_id=mcp_record.get("user_id"),
                            env=mcp_record.get("env"),
                        )
                    return {"success": False, "error": f"Device removal failed: {err}"}

            # 触发 scope 同步（scope 失败不回滚，设备端已移除即可）
            if user_id:
                scope_result = await self.refresh_mcp_scope(user_id=user_id)
                if not scope_result.get("success"):
                    err = scope_result.get("error", "Unknown error")
                    logger.error("[remove_mcp_from_skill_set] 刷新 MCP 授权范围失败: %s", err)
                    return {"success": False, "error": f"Scope sync failed: {err}"}

            return {"success": True, "error": None}

    def get_set_mcp_servers(
        self,
        skill_set_id: str,
        user_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        engine_type: Optional[str] = None,
    ) -> List[dict]:
        """Get all MCP servers in a skill set.

        For default skill sets, merges DEFAULT_MCP_SERVERS_CONFIG and filters out
        user-excluded MCPs from ac_default_skillset_mcp_exclusion.

        Args:
            skill_set_id: Skill set ID
            user_id: User ID for exclusion lookup
            bot_id: Bot ID for exclusion lookup (required for default skill sets)
            engine_type: Engine type for default MCP list. Defaults to self.engine_type.
        """
        skill_set = self.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            return []

        effective_engine = engine_type if engine_type is not None else self.engine_type

        # Get associations from DB (contains server_code and name)
        associations = self.skill_set_repo.get_mcp_servers_in_set(skill_set_id)

        # If this is a default skill set, merge with DEFAULT_MCP_SERVERS_CONFIG
        if skill_set.get('is_default'):
            default_codes = get_default_mcp_server_codes(effective_engine)
            excluded_codes = set()

            # Use provided bot_id, fallback to self.bot_id, then None
            effective_bot_id = bot_id if bot_id is not None else self.bot_id
            if user_id and effective_bot_id:
                excluded_codes = set(
                    self.skill_set_repo.get_excluded_mcps(user_id, effective_bot_id, int(skill_set_id))
                )

            db_codes = {a.get("server_code") for a in associations}
            logger.info(
                f"[get_set_mcp_servers] skill_set_id={skill_set_id}, is_default=True, "
                f"engine_type={effective_engine}, db_codes={list(db_codes)}, "
                f"default_codes={default_codes}, excluded_codes={list(excluded_codes)}"
            )

            for code in default_codes:
                if code in excluded_codes:
                    continue  # Skip user-excluded
                if code not in db_codes:
                    # Add default MCP (not in DB)
                    # 分配 mock id，避免前端因 id 为 None 导致 checkbox key 冲突
                    # 用 adler32 保证同一 server_code 的 mock id 稳定，不受列表顺序/排除项影响
                    mock_id = (zlib.adler32(code.encode("utf-8")) % 99999) + 1
                    default_cfg = get_default_mcp_config(effective_engine, code) or {}
                    associations.append({
                        "id": mock_id,
                        "server_code": code,
                        "name": default_cfg.get("name") or code.split(".")[-1],
                        "description": default_cfg.get("description", "默认 MCP"),
                        "icon": default_cfg.get("icon"),
                        "is_default": True,
                    })

        # Convert to expected format
        result = []
        for assoc in associations:
            result.append({
                "id": assoc.get("id"),
                "server_code": assoc.get("server_code"),
                "name": assoc.get("name"),
                "description": assoc.get("description"),
                "icon": assoc.get("icon"),
                "status": "ONLINE",  # Default status since we don't store full data locally
                "is_default": assoc.get("is_default", False),
            })
        logger.info(
            f"[get_set_mcp_servers] skill_set_id={skill_set_id}, is_default={skill_set.get('is_default')}, "
            f"engine_type={effective_engine}, result_codes={[r.get('server_code') for r in result]}"
        )
        return result

    def collect_bot_active_mcps(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
    ) -> List[dict]:
        """收集 Bot 关联的激活 MCP（激活 SkillSet + 默认），不调用 MCP Center 补全数据。

        Args:
            engine_type: Engine type for scoping. Defaults to self.engine_type.
        """
        effective_engine = engine_type if engine_type is not None else self.engine_type
        active_skill_sets = self.skill_set_repo.get_all_active_skill_sets(
            user_id=entity_id,
            bolt_id=bot_id,
            engine_type=effective_engine,
        )

        active_mcps = []
        seen_server_codes = set()
        skill_set_mcps_detail = []
        for skill_set in active_skill_sets:
            skill_set_id = str(skill_set.get("id"))
            skill_set_name = skill_set.get("name", "unnamed")
            mcps_in_set = self.get_set_mcp_servers(skill_set_id, user_id, bot_id, effective_engine)
            skill_mcp_codes = []
            for mcp in mcps_in_set:
                server_code = mcp.get("server_code")
                skill_mcp_codes.append(server_code)
                if server_code and server_code not in seen_server_codes:
                    seen_server_codes.add(server_code)
                    active_mcps.append(mcp)
            skill_set_mcps_detail.append(f"{skill_set_name}[{len(skill_mcp_codes)}]: {skill_mcp_codes}")
        logger.info(f"[collect_bot_active_mcps] bot_id={bot_id}, engine_type={effective_engine}, skillsets MCPs: {'; '.join(skill_set_mcps_detail)}")

        # Get user-excluded default MCPs (across all default skill sets)
        excluded_codes = set(self.skill_set_repo.get_all_excluded_mcps(user_id, bot_id))

        default_mcp_configs = get_default_mcp_servers(effective_engine)
        default_mcps = []
        for config in default_mcp_configs:
            server_code = config["server_code"]
            if server_code in excluded_codes:
                continue  # Skip user-excluded default MCPs
            mcp_entry = {
                "server_code": server_code,
                "name": config.get("name") or server_code,
                "description": config.get("description", "Default MCP"),
                "status": "ONLINE",
            }
            if "icon" in config and config.get("icon"):
                mcp_entry["icon"] = config["icon"]
            if "headers" in config:
                mcp_entry["headers"] = config["headers"]
            default_mcps.append(mcp_entry)

        all_mcps = list(active_mcps)
        for default_mcp in default_mcps:
            if default_mcp["server_code"] not in seen_server_codes:
                all_mcps.append(default_mcp)

        logger.info(
            f"[collect_bot_active_mcps] bot_id={bot_id}, engine_type={effective_engine}, "
            f"total_mcps={len(all_mcps)}, codes={[m.get('server_code') for m in all_mcps]}"
        )
        return all_mcps

    def collect_bot_mcps(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
    ) -> List[dict]:
        """收集 Bot 关联的所有 MCP（所有 SkillSet + 默认），不调用 MCP Center 补全数据。

        Args:
            engine_type: Engine type for scoping. Defaults to self.engine_type.
        """
        effective_engine = engine_type if engine_type is not None else self.engine_type
        all_skill_sets = self.list_skill_sets(
            user_id=entity_id,
            bolt_id=bot_id,
        )

        active_mcps = []
        seen_server_codes = set()
        skill_set_mcps_detail = []
        for skill_set in all_skill_sets:
            skill_set_id = str(skill_set.get("id"))
            skill_set_name = skill_set.get("name", "unnamed")
            mcps_in_set = self.get_set_mcp_servers(skill_set_id, user_id, bot_id, effective_engine)
            skill_mcp_codes = []
            for mcp in mcps_in_set:
                server_code = mcp.get("server_code")
                skill_mcp_codes.append(server_code)
                if server_code and server_code not in seen_server_codes:
                    seen_server_codes.add(server_code)
                    active_mcps.append(mcp)
            skill_set_mcps_detail.append(f"{skill_set_name}[{len(skill_mcp_codes)}]: {skill_mcp_codes}")
        logger.info(f"[collect_bot_mcps] bot_id={bot_id}, engine_type={effective_engine}, skillsets MCPs: {'; '.join(skill_set_mcps_detail)}")

        # Get user-excluded default MCPs (across all default skill sets)
        excluded_codes = set(self.skill_set_repo.get_all_excluded_mcps(user_id, bot_id))

        default_mcp_configs = get_default_mcp_servers(effective_engine)
        default_mcps = []
        for config in default_mcp_configs:
            server_code = config["server_code"]
            if server_code in excluded_codes:
                continue  # Skip user-excluded default MCPs
            mcp_entry = {
                "server_code": server_code,
                "name": config.get("name") or server_code,
                "description": config.get("description", "Default MCP"),
                "status": "ONLINE",
            }
            if "icon" in config and config.get("icon"):
                mcp_entry["icon"] = config["icon"]
            if "headers" in config:
                mcp_entry["headers"] = config["headers"]
            default_mcps.append(mcp_entry)

        all_mcps = list(active_mcps)
        for default_mcp in default_mcps:
            if default_mcp["server_code"] not in seen_server_codes:
                all_mcps.append(default_mcp)

        logger.info(
            f"[collect_bot_mcps] bot_id={bot_id}, engine_type={effective_engine}, "
            f"total_mcps={len(all_mcps)}, codes={[m.get('server_code') for m in all_mcps]}"
        )
        return all_mcps

    def get_bot_mcp_codes(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
    ) -> List[str]:
        """获取 Bot 关联的所有 MCP 的 server_code 列表。"""
        mcps = self.collect_bot_active_mcps(entity_id, bot_id, user_id, entity_type, engine_type)
        return [m.get("server_code") for m in mcps if m.get("server_code")]

    def get_bot_mcp_codes_for_env(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str,
        engine_type: str | None,
        target_env: str,
    ) -> list[str]:
        """Collect Passport MCP scope from explicitly env-scoped skill-set data."""
        if target_env not in {"pre", "prod"}:
            raise ValueError("target_env must be pre or prod")
        effective_engine = engine_type if engine_type is not None else self.engine_type
        active_skill_sets = self.skill_set_repo.get_all_active_skill_sets_for_env(
            user_id=entity_id,
            bolt_id=bot_id,
            engine_type=effective_engine,
            env=target_env,
        )

        codes: list[str] = []
        seen: set[str] = set()
        for skill_set in active_skill_sets:
            associations = self.skill_set_repo.get_mcp_servers_in_set_for_env(
                str(skill_set["id"]), env=target_env
            )
            for association in associations:
                code = association.get("server_code")
                if code and code not in seen:
                    seen.add(code)
                    codes.append(code)

        excluded_codes = set(
            self.skill_set_repo.get_all_excluded_mcps(user_id, bot_id)
        )
        for default_mcp in get_default_mcp_servers(effective_engine):
            code = default_mcp["server_code"]
            if code not in excluded_codes and code not in seen:
                seen.add(code)
                codes.append(code)
        return codes

    def get_active_skill_sets_mcp_summary(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Return active skill sets info plus duplicate server codes.

        Args:
            engine_type: Engine type for scoping (e.g., 'openclaw', 'aicoding').
        """
        effective_engine = engine_type if engine_type is not None else self.engine_type
        active_skill_sets = self.skill_set_repo.get_all_active_skill_sets(
            user_id=entity_id,
            bolt_id=bot_id,
            engine_type=effective_engine,
        )
        active_skill_sets_info = []
        seen = set()
        duplicate_server_codes = []
        for skill_set in active_skill_sets:
            skill_set_id = str(skill_set.get("id"))
            skill_set_name = skill_set.get("name", "")
            mcps_in_set = self.get_set_mcp_servers(skill_set_id, user_id, bot_id, effective_engine)
            active_skill_sets_info.append({
                "id": skill_set_id,
                "name": skill_set_name,
                "mcp_count": len(mcps_in_set),
            })
            for mcp in mcps_in_set:
                sc = mcp.get("server_code")
                if sc and sc not in seen:
                    seen.add(sc)
                elif sc:
                    duplicate_server_codes.append(sc)
        return active_skill_sets_info, duplicate_server_codes

    async def refresh_mcp_scope(
        self,
        user_id: str,
        engine_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """刷新MCP授权范围（异步方法）。

        向设备声明filter-servers白名单，并更新passport的MCP codes列表。
        不包含MCP详细配置的推送——那是 sync_mcp_details / sync_mcp_detail 的职责。

        Args:
            user_id: User ID for database queries (required, cannot be empty)
            engine_type: Engine type for scoping. If None, falls back to Bot.active_engine.

        Returns:
            ``{"success": bool, "error": str|None}`` 格式的结果字典。
        """
        if not user_id:
            raise ValueError("user_id is required and cannot be empty")

        # Fallback to Bot.active_engine if not provided
        effective_engine = engine_type
        if not effective_engine:
            try:
                bot = self._bot_repo.get_by_id_and_owner(self.bot_id, self.entity_id)
                if bot:
                    effective_engine = bot.get("active_engine") or "openclaw"
            except Exception as e:
                logger.warning(f"[SkillSetService] Failed to get bot active_engine: {e}")
            effective_engine = effective_engine or "openclaw"

        return await self._mcp_sync_service.refresh_mcp_scope(
            user_id=user_id,
            entity_id=self.entity_id,
            bot_id=self.bot_id,
            entity_type=self.entity_type,
            engine_type=effective_engine,
        )


# ======
# SkillSet Switcher - 技能集切换功能
# ======

@dataclass
class SwitchResult:
    """Result of a skill set switch operation."""
    success: bool
    message: str
    activated: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)


class _DeviceSyncMixin:
    """Shared device sync helpers for SkillSetSwitcher and SkillSetActivator."""

    _resolver: "DeviceContextResolver"
    _device_sync_dispatcher: "DeviceSyncDispatcher"
    _device_plugin: DeviceAccessor
    _edit_guard: SkillsPoolEditGuard
    skill_set_service: SkillSetService

    def _bot_layout_scope(self, user_id: str | None) -> BotSkillLayoutScope | None:
        """Resolve a Bot lock by owner, while retaining its entity as scope."""
        owner_id = (
            user_id
            or self.skill_set_service.user_id
            or self.skill_set_service.entity_id
        )
        bot_id = self.skill_set_service.bot_id
        if not owner_id or not bot_id:
            return None
        bot = self.skill_set_service._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None or bot.get("entity_id") is None or bot.get("env") is None:
            return None
        return BotSkillLayoutScope(
            env=str(bot["env"]),
            entity_id=str(bot["entity_id"]),
            bot_id=str(bot_id),
        )

    def _do_device_sync(self, user_id: str | None, caller: str = "DeviceSyncMixin") -> dict:
        """Sync symlink mappings to device via DeviceSyncPlugin.

        Returns:
            dict: {"success": bool, "message": str, "error": str|None}
        """
        bot_id = self.skill_set_service.bot_id
        logger.info(f"[DEVICE-PLUGIN-DEBUG] {caller}._do_device_sync: bot_id={bot_id}, user_id={user_id}")
        if not bot_id or not user_id:
            logger.info(f"[DEVICE-PLUGIN-DEBUG] {caller}._do_device_sync: SKIP (missing bot_id or user_id)")
            return {"success": False, "message": "Skipped", "error": "missing bot_id or user_id"}
        try:
            ctx = self._resolver.resolve_for_bot(bot_id, user_id)
            device_sync = self._device_sync_dispatcher.dispatch(ctx)
            logger.info(f"[DEVICE-PLUGIN-DEBUG] {caller}._do_device_sync: factory returned {type(device_sync).__name__}")
            symlinks = self.skill_set_service.get_symlink_mappings(
                user_id=user_id,
                bolt_id=bot_id,
            )
            symlinks_dict = [sm.to_dict() for sm in symlinks]
            logger.info(f"[DEVICE-PLUGIN-DEBUG] {caller}._do_device_sync: {len(symlinks_dict)} symlinks to sync")
            sync_result = device_sync.sync_symlinks(symlinks_dict)
            logger.info(f"[DEVICE-PLUGIN-DEBUG] {caller}._do_device_sync: result={sync_result}")

            # 检查同步结果
            if not sync_result.get("success"):
                error_msg = sync_result.get("message", "Unknown error")
                logger.error(f"[DEVICE-PLUGIN-DEBUG] {caller}._do_device_sync: SYNC FAILED: {error_msg}")
                return {"success": False, "message": error_msg, "error": error_msg}

            return {"success": True, "message": sync_result.get("message", "OK"), "error": None}
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[DEVICE-PLUGIN-DEBUG] {caller}._do_device_sync: EXCEPTION: {e}")
            return {"success": False, "message": error_msg, "error": error_msg}


class SkillSetSwitcherFactory:
    """Mints :class:`SkillSetSwitcher` instances per-request.

    Wired via ``@provider`` in ``SkillCenterModule`` because the ctor
    types ``SkillSetServiceFactory`` / ``DeviceContextResolver`` /
    ``DeviceSyncDispatcher`` as TYPE_CHECKING forward refs.
    """

    def __init__(
        self,
        skill_set_factory: "SkillSetServiceFactory",
        resolver: "DeviceContextResolver",
        device_sync_dispatcher: "DeviceSyncDispatcher",
        device_plugin: DeviceAccessor,
        path_factory: WorkspacePathFactory,
        device_fs_dispatcher: "DeviceFilesystemDispatcher",
        edit_guard: SkillsPoolEditGuard,
    ) -> None:
        self._skill_set_factory = skill_set_factory
        self._resolver = resolver
        self._device_sync_dispatcher = device_sync_dispatcher
        self._device_plugin = device_plugin
        self._path_factory = path_factory
        self._device_fs_dispatcher = device_fs_dispatcher
        self._edit_guard = edit_guard

    def create(
        self,
        *,
        entity_id: str | None = None,
        bot_id: str | None = None,
        engine_type: str | None = None,
        user_id: str | None = None,
    ) -> "SkillSetSwitcher":
        return SkillSetSwitcher(
            skill_set_factory=self._skill_set_factory,
            resolver=self._resolver,
            device_sync_dispatcher=self._device_sync_dispatcher,
            device_plugin=self._device_plugin,
            path_factory=self._path_factory,
            device_fs_dispatcher=self._device_fs_dispatcher,
            edit_guard=self._edit_guard,
            entity_id=entity_id,
            bot_id=bot_id,
            engine_type=engine_type,
            user_id=user_id,
        )


class SkillSetSwitcher(_DeviceSyncMixin):
    """Service for switching between skill sets at the system level."""

    def __init__(
        self,
        skill_set_factory: "SkillSetServiceFactory",
        resolver: "DeviceContextResolver",
        device_sync_dispatcher: "DeviceSyncDispatcher",
        device_plugin: DeviceAccessor,
        *,
        path_factory: WorkspacePathFactory,
        device_fs_dispatcher: "DeviceFilesystemDispatcher",
        edit_guard: SkillsPoolEditGuard,
        skills_dir: Path | None = None,
        repo_dir: Path | None = None,
        local_dir: Path | None = None,
        user_id: str | None = None,
        entity_id: str | None = None,
        bot_id: str | None = None,
        engine_type: str | None = None,
    ):
        self._resolver = resolver
        self._device_sync_dispatcher = device_sync_dispatcher
        self._device_plugin = device_plugin
        self._device_fs_dispatcher = device_fs_dispatcher
        self._edit_guard = edit_guard
        # Cache user/owner/bot for plugin retrieval at cleanup-time.
        self._user_id_for_dispatcher = user_id
        self._entity_id_for_dispatcher = entity_id
        self._bot_id_for_dispatcher = bot_id

        # Use new path structure if any path params provided, otherwise fall back to deprecated params
        if user_id or entity_id:
            self.skills_dir, self.repo_dir, self.local_dir = _get_bot_paths(
                path_factory=path_factory,
                user_id=user_id,
                entity_id=entity_id,
                bot_id=bot_id,
                engine_type=engine_type,
            )
        else:
            self.skills_dir = skills_dir or SKILLS_DIR
            self.repo_dir = repo_dir or SKILLS_REPO_DIR
            self.local_dir = local_dir or SKILLS_LOCAL_DIR

        self.CURRENT_SET_FILE = self.skills_dir / ".current_skill_set"

        self.skill_set_service = skill_set_factory.create(
            skills_dir=self.skills_dir, repo_dir=self.repo_dir, local_dir=self.local_dir,
            user_id=user_id, entity_id=entity_id, bot_id=bot_id, engine_type=engine_type
        )
        if isinstance(self.skill_set_service.skills_dir, Path):
            self.skills_dir = self.skill_set_service.skills_dir
            self.repo_dir = self.skill_set_service.repo_dir
            self.local_dir = self.skill_set_service.local_dir
            self.CURRENT_SET_FILE = self.skills_dir / ".current_skill_set"

    def get_current_skill_set(self) -> dict[str, Any] | None:
        """Get the currently active skill set."""
        if not self.CURRENT_SET_FILE.exists():
            return None

        try:
            data = json.loads(self.CURRENT_SET_FILE.read_text())
            skill_set_id = data.get("skill_set_id")
            if skill_set_id:
                skill_set = self.skill_set_service.get_skill_set(skill_set_id)
                if skill_set:
                    return {
                        "skill_set_id": skill_set['id'],
                        "name": skill_set['name'],
                        "description": skill_set.get('description'),
                        "switched_at": data.get("switched_at")
                    }
        except Exception as e:
            logger.warning(f"Error reading current skill set: {e}")

        return None

    def _save_current_skill_set(self, skill_set_id: str) -> None:
        """Save current skill set to file."""
        data = {
            "skill_set_id": skill_set_id,
            "switched_at": datetime.utcnow().isoformat()
        }
        self.CURRENT_SET_FILE.write_text(json.dumps(data, indent=2))

    def _clear_current_skill_set(self) -> None:
        """Clear current skill set file."""
        if self.CURRENT_SET_FILE.exists():
            self.CURRENT_SET_FILE.unlink()

    def _get_active_skill_ids(self) -> list[str]:
        """Get list of currently active skill IDs (from symlinks)."""
        active_skills = self.skill_set_service.skill_service.get_active_skills()
        return [s.id for s in active_skills]

    def _cleanup_all_non_reserved_items(self) -> list[str]:
        """
        清理 skills 目录下所有非保留项目（通过 DeviceFileSystem 走 BaaS）。

        改造前直接 ``skills_dir.iterdir() + shutil.rmtree``；plan-05 改为
        通过 ``device_fs_dispatcher.for_bot(...)`` 拿到的 plugin 来 list/delete，
        singlebox 路径就会自动走 BaaS HTTP，pathlib 模式（contract test 用）
        仍然 work（plugin 内部 dual-mode 决定）。

        保留项目：
        - skills-repo: 技能仓库目录
        - skills-local: 本地上传技能目录
        - .current_skill_set: 当前技能集标记文件
        - skill_sets.json: 技能集配置文件

        Returns:
            已清理的项目名称列表
        """
        import asyncio

        cleaned: list[str] = []
        skills_dir = self.skills_dir

        bot_id = self._bot_id_for_dispatcher or self.skill_set_service.bot_id
        user_id = (
            self._user_id_for_dispatcher
            or self._entity_id_for_dispatcher
            or self.skill_set_service.entity_id
        )
        if not bot_id or not user_id:
            logger.info(
                "[SkillSetSwitcher] cleanup skipped — no bot_id/user_id for dispatcher"
            )
            return cleaned

        ctx = self._resolver.resolve_for_bot(bot_id, user_id)
        device_fs = self._device_fs_dispatcher.dispatch(ctx)
        reserved_names = self.skill_set_service.skill_service.RESERVED_SKILL_NAMES

        async def _do_cleanup() -> list[str]:
            entries = await device_fs.list_dir(str(skills_dir))
            if entries is None:
                logger.info(
                    f"[SkillSetSwitcher] skills_dir does not exist (plugin returned None): {skills_dir}"
                )
                return []
            local_cleaned: list[str] = []
            for entry in entries:
                name = entry.get("name", "")
                if name in reserved_names:
                    logger.debug(f"[SkillSetSwitcher] Skipping reserved item: {name}")
                    continue
                entry_path = entry.get("path") or f"{skills_dir}/{name}"
                try:
                    ok = await device_fs.delete_tree(entry_path)
                    if ok:
                        local_cleaned.append(name)
                        logger.info(f"[SkillSetSwitcher] Removed: {name}")
                except Exception as e:
                    logger.warning(f"[SkillSetSwitcher] Failed to remove {name}: {e}")
            return local_cleaned

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            future = asyncio.run_coroutine_threadsafe(_do_cleanup(), loop)
            cleaned = future.result(timeout=60)
        else:
            cleaned = asyncio.run(_do_cleanup())

        logger.info(f"[SkillSetSwitcher] Cleaned {len(cleaned)} items: {cleaned}")
        return cleaned

    async def switch_to_skill_set(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        proxy_token: str | None = None
    ) -> SwitchResult:
        """Serialize a Bot-scoped switch with Local Skill mutations."""
        scope = self._bot_layout_scope(user_id)
        if scope is None:
            return await self._switch_to_skill_set_unlocked(
                skill_set_id, user_id=user_id, proxy_token=proxy_token
            )
        try:
            lease = await self._edit_guard.acquire_for_edit_wait(scope=scope)
        except SkillsPoolEditPausedError:
            return SwitchResult(
                success=False,
                message="Skills are temporarily read-only while layout work is running",
            )
        try:
            return await self._switch_to_skill_set_unlocked(
                skill_set_id, user_id=user_id, proxy_token=proxy_token
            )
        finally:
            self._edit_guard.release(lease)

    async def _switch_to_skill_set_unlocked(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        proxy_token: str | None = None,
    ) -> SwitchResult:
        """Switch to a new skill set."""
        result = SwitchResult(success=False, message="")

        logger.info(f"[SkillSetSwitcher] Starting switch to skill_set_id={skill_set_id}, user_id={user_id}, bot_id={self.skill_set_service.bot_id}")

        # Verify skill set exists
        skill_set = self.skill_set_service.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            result.message = f"Skill set '{skill_set_id}' not found"
            logger.warning(f"[SkillSetSwitcher] Skill set {skill_set_id} not found")
            return result

        logger.info(f"[SkillSetSwitcher] Found skill set: id={skill_set.get('id')}, name={skill_set.get('name')}, is_default={skill_set.get('is_default')}, user_id={skill_set.get('user_id')}")

        # Step 1: Clean ALL non-reserved items (symlinks, real directories, files)
        logger.info("[SkillSetSwitcher] Cleaning all non-reserved items in skills directory")
        cleaned_items = self._cleanup_all_non_reserved_items()
        result.deactivated = cleaned_items  # Track cleaned items
        logger.info(f"[SkillSetSwitcher] Cleaned items: {cleaned_items}")

        # Step 2: Get skills in the new skill set
        new_skills = self.skill_set_service.get_set_skills(skill_set_id, user_id)

        # Step 3: Activate all skills in new set concurrently
        # 修复: 传递 user_id 和 bolt_id 到 activate_skill，确保设备操作正确路由
        # 获取 Bot 的 owner_id（用于设备绑定查询）
        bot = self.skill_set_service._bot_repo.get_by_id(self.skill_set_service.bot_id)
        owner_id = bot.get("owner_id") if bot else None
        if not owner_id:
            owner_id = self.skill_set_service.entity_id or user_id

        skill_infos = [(skill.get('id'), skill.get('git_path')) for skill in new_skills]
        if skill_infos:
            activations = await asyncio.gather(
                *[
                    self.skill_set_service.skill_service.activate_skill(
                        git_path,
                        user_id=owner_id,
                        bolt_id=self.skill_set_service.bot_id,
                    )
                    for _, git_path in skill_infos
                    if git_path
                ],
                return_exceptions=True
            )
            # Pair results with skill_ids, handling cases where git_path was None
            idx = 0
            for skill_id, git_path in skill_infos:
                if not git_path:
                    result.failed.append({"skill_id": skill_id, "action": "activate", "error": "No git_path defined"})
                    continue
                act_result = activations[idx]
                idx += 1
                if isinstance(act_result, Exception):
                    result.failed.append({"skill_id": skill_id, "action": "activate", "error": str(act_result)})
                elif act_result:
                    result.activated.append(skill_id)
                else:
                    result.failed.append({"skill_id": skill_id, "action": "activate", "error": "Failed to activate"})

        # Save current skill set
        skill_set_name = skill_set.get('name', 'Unknown')
        logger.info(f"[SkillSetSwitcher] Setting active skill set: skill_set_id={skill_set_id}, user_id={user_id}, bot_id={self.skill_set_service.bot_id}")
        if len(result.failed) == 0:
            # 更新数据库中的 is_active 字段
            self.skill_set_service.skill_set_repo.set_active_skill_set(
                skill_set_id=skill_set_id,
                user_id=user_id,
                bolt_id=self.skill_set_service.bot_id
            )
            logger.info("[SkillSetSwitcher] Successfully set active skill set in DB")
            # 保留文件写入作为备份（兼容性）
            self._save_current_skill_set(skill_set_id)
            result.success = True
            result.message = f"Successfully switched to '{skill_set_name}'"
        else:
            # 更新数据库中的 is_active 字段
            self.skill_set_service.skill_set_repo.set_active_skill_set(
                skill_set_id=skill_set_id,
                user_id=user_id,
                bolt_id=self.skill_set_service.bot_id
            )
            logger.info(f"[SkillSetSwitcher] Set active skill set in DB (with {len(result.failed)} skill activation failures)")
            # 保留文件写入作为备份（兼容性）
            self._save_current_skill_set(skill_set_id)
            result.success = True  # Partial success
            result.message = f"Switched to '{skill_set_name}' with {len(result.failed)} failures"

        logger.info(f"[SkillSetSwitcher] Switch completed: success={result.success}, message={result.message}")

        # Update metadata file
        SkillSetMetadataWriter(skill_set_repo=self.skill_set_service.skill_set_repo, skill_repo=self.skill_set_service.skill_repo, skills_dir=self.skills_dir, user_id=user_id, bot_id=self.skill_set_service.bot_id).write_metadata()

        # 同步软链到远程设备（通过 DeviceSyncPlugin）
        if result.success:
            sync_result = self._do_device_sync(user_id, caller="SkillSetSwitcher.switch")
            if not sync_result.get("success"):
                # 软链同步失败，标记为部分成功
                result.success = False
                result.message = f"技能集切换成功，但软链同步失败: {sync_result.get('error', 'Unknown error')}"
                result.failed.append({"error": "device_sync_failed", "message": sync_result.get("error", "Unknown error")})
                logger.error(f"[SkillSetSwitcher.switch] Device sync failed: {sync_result}")

        # 触发轻量同步（filter-servers，更新 allowServers 列表）
        if result.success:
            scope_result = await self.skill_set_service.refresh_mcp_scope(user_id=user_id)
            if not scope_result.get("success"):
                error = scope_result.get("error", "Unknown error")
                result.success = False
                result.message = f"技能集切换成功，但 MCP 授权范围刷新失败: {error}"
                result.failed.append({"error": "mcp_scope_sync_failed", "message": error})
                logger.error("[SkillSetSwitcher.switch] 刷新 MCP 授权范围失败: %s", error)
            else:
                logger.info("[SkillSetSwitcher.switch] Light sync done")
                detail_result = await self.skill_set_service._mcp_sync_service.sync_mcp_details(
                    user_id=user_id,
                    entity_id=self.skill_set_service.entity_id,
                    bot_id=self.skill_set_service.bot_id,
                    entity_type=self.skill_set_service.entity_type,
                    engine_type=self.skill_set_service.engine_type,
                    active_only=True,
                )
                if not detail_result.get("success"):
                    error = detail_result.get("error", "Unknown error")
                    result.success = False
                    result.message = f"技能集切换成功，但 MCP 配置同步失败: {error}"
                    result.failed.append({"error": "mcp_detail_sync_failed", "message": error})
                    logger.error("[SkillSetSwitcher.switch] 激活 MCP 详情同步失败: %s", error)

        return result

    async def deactivate_all_skills(self) -> SwitchResult:
        """Deactivate all currently active skills."""
        result = SwitchResult(success=False, message="")

        current_active = self._get_active_skill_ids()

        if not current_active:
            result.success = True
            result.message = "No active skills to deactivate"
            return result

        for skill_id in current_active:
            try:
                success = await self.skill_set_service.skill_service.deactivate_skill(
                    skill_id, bolt_id=self.skill_set_service.bot_id, user_id=self.skill_set_service.entity_id
                )
                if success:
                    result.deactivated.append(skill_id)
                else:
                    result.failed.append({"skill_id": skill_id, "action": "deactivate", "error": "Failed to deactivate"})
            except Exception as e:
                result.failed.append({"skill_id": skill_id, "action": "deactivate", "error": str(e)})

        if len(result.failed) == 0:
            # 清除数据库中的激活状态
            self.skill_set_service.skill_set_repo.clear_active_skill_set(
                user_id=self.skill_set_service.entity_id,
                bolt_id=self.skill_set_service.bot_id
            )
            # 保留文件移除作为备份
            self._clear_current_skill_set()
            result.success = True
            result.message = f"Successfully deactivated {len(result.deactivated)} skills"
        else:
            result.success = len(result.deactivated) > 0
            result.message = f"Deactivated {len(result.deactivated)} skills, {len(result.failed)} failed"

        return result

    async def sync_skill_set_to_active(
        self, skill_set_id: str, user_id: str | None = None
    ) -> SwitchResult:
        """Serialize an additive Bot sync with Local Skill mutations."""
        scope = self._bot_layout_scope(user_id)
        if scope is None:
            return await self._sync_skill_set_to_active_unlocked(skill_set_id, user_id)
        try:
            lease = await self._edit_guard.acquire_for_edit_wait(scope=scope)
        except SkillsPoolEditPausedError:
            return SwitchResult(
                success=False,
                message="Skills are temporarily read-only while layout work is running",
            )
        try:
            return await self._sync_skill_set_to_active_unlocked(skill_set_id, user_id)
        finally:
            self._edit_guard.release(lease)

    async def _sync_skill_set_to_active_unlocked(
        self, skill_set_id: str, user_id: str | None = None
    ) -> SwitchResult:
        """Sync a skill set to active skills without deactivating others."""
        result = SwitchResult(success=False, message="")

        skill_set = self.skill_set_service.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            result.message = f"Skill set '{skill_set_id}' not found"
            return result

        current_active = self._get_active_skill_ids()
        set_skills = self.skill_set_service.get_set_skills(skill_set_id, user_id)

        # Pre-compute activation tasks for skills not yet active
        tasks = []  # (skill_id, skill_path, source_path)
        for skill in set_skills:
            skill_id = skill.get('id')
            if skill_id in current_active:
                continue  # Already active
            skill_path = skill.get('git_path') or skill.get('skill_path')
            if not skill_path:
                result.failed.append({"skill_id": skill_id, "action": "activate", "error": "No skill_path or git_path defined"})
                continue
            # Determine source path based on skill_path type
            if skill_path.startswith("local://"):
                skill_name = skill_path[8:]
                source_path = str(self.skill_set_service.skill_service.local_dir / skill_name)
            elif skill_path.startswith("git://"):
                git_rel_path = skill_path[6:]
                source_path = str(self.skill_set_service.skill_service.repo_dir / git_rel_path)
            else:
                source_path = str(self.skill_set_service.skill_service.repo_dir / skill_path)
            tasks.append((skill_id, skill_path, source_path))

        # 修复: 传递 user_id 和 bolt_id 到 activate_skill，确保设备操作正确路由
        # 获取 Bot 的 owner_id（用于设备绑定查询）
        bot = self.skill_set_service._bot_repo.get_by_id(self.skill_set_service.bot_id)
        owner_id = bot.get("owner_id") if bot else None
        if not owner_id:
            owner_id = self.skill_set_service.entity_id or user_id

        # Activate all concurrently
        if tasks:
            activations = await asyncio.gather(
                *[
                    self.skill_set_service.skill_service.activate_skill(
                        skill_path,
                        user_id=owner_id,
                        bolt_id=self.skill_set_service.bot_id,
                    )
                    for _, skill_path, _ in tasks
                ],
                return_exceptions=True
            )
            for (skill_id, skill_path, source_path), act_result in zip(tasks, activations):
                if isinstance(act_result, Exception):
                    result.failed.append({"skill_id": skill_id, "action": "activate", "error": str(act_result)})
                elif act_result:
                    result.activated.append(skill_id)
                else:
                    result.failed.append({"skill_id": skill_id, "action": "activate", "error": "Failed to activate"})

        self._save_current_skill_set(skill_set_id)
        # 更新数据库中的 is_active 字段
        self.skill_set_service.skill_set_repo.set_active_skill_set(
            skill_set_id=skill_set_id,
            user_id=user_id,
            bolt_id=self.skill_set_service.bot_id
        )

        skill_set_name = skill_set.get('name', 'Unknown')
        if len(result.failed) == 0:
            result.success = True
            result.message = f"Successfully synced '{skill_set_name}' - activated {len(result.activated)} skills"
        else:
            result.success = len(result.activated) > 0
            result.message = f"Synced '{skill_set_name}' - activated {len(result.activated)}, {len(result.failed)} failed"

        return result


# ======
# SkillSet Activator - 技能集激活功能（支持多能力集激活）
# ======

@dataclass
class ActivateResult:
    """Result of a skill set activate operation."""
    success: bool
    message: str
    activated: list[str] = field(default_factory=list)  # 激活的技能 ID 列表
    failed: list[dict[str, str]] = field(default_factory=list)


@dataclass
class DeactivateResult:
    """Result of a skill set deactivate operation."""
    success: bool
    message: str
    deactivated: list[str] = field(default_factory=list)  # 取消激活的技能 ID 列表
    failed: list[dict[str, str]] = field(default_factory=list)


class SkillSetActivatorFactory:
    """Mints :class:`SkillSetActivator` instances per-request.

    Same pattern as ``SkillSetSwitcherFactory`` — wired via ``@provider``
    because the ctor types ``SkillSetServiceFactory`` /
    ``DeviceContextResolver`` / ``DeviceSyncDispatcher`` as TYPE_CHECKING
    forward refs.
    """

    def __init__(
        self,
        skill_set_factory: "SkillSetServiceFactory",
        resolver: "DeviceContextResolver",
        device_sync_dispatcher: "DeviceSyncDispatcher",
        device_plugin: DeviceAccessor,
        path_factory: WorkspacePathFactory,
        edit_guard: SkillsPoolEditGuard,
    ) -> None:
        self._skill_set_factory = skill_set_factory
        self._resolver = resolver
        self._device_sync_dispatcher = device_sync_dispatcher
        self._device_plugin = device_plugin
        self._path_factory = path_factory
        self._edit_guard = edit_guard

    def create(
        self,
        *,
        entity_id: str | None = None,
        bot_id: str | None = None,
        engine_type: str | None = None,
        user_id: str | None = None,
    ) -> "SkillSetActivator":
        return SkillSetActivator(
            skill_set_factory=self._skill_set_factory,
            resolver=self._resolver,
            device_sync_dispatcher=self._device_sync_dispatcher,
            device_plugin=self._device_plugin,
            path_factory=self._path_factory,
            edit_guard=self._edit_guard,
            entity_id=entity_id,
            bot_id=bot_id,
            engine_type=engine_type,
            user_id=user_id,
        )


class SkillSetActivator(_DeviceSyncMixin):
    """技能集激活管理器 - 支持多能力集同时激活"""

    def __init__(
        self,
        skill_set_factory: "SkillSetServiceFactory",
        resolver: "DeviceContextResolver",
        device_sync_dispatcher: "DeviceSyncDispatcher",
        device_plugin: DeviceAccessor,
        *,
        path_factory: WorkspacePathFactory,
        edit_guard: SkillsPoolEditGuard,
        user_id: str | None = None,
        entity_id: str | None = None,
        bot_id: str | None = None,
        engine_type: str | None = None,
    ):
        self._resolver = resolver
        self._device_sync_dispatcher = device_sync_dispatcher
        self._device_plugin = device_plugin
        self._edit_guard = edit_guard

        # Use new path structure
        if user_id or entity_id:
            self.skills_dir, self.repo_dir, self.local_dir = _get_bot_paths(
                path_factory=path_factory,
                user_id=user_id,
                entity_id=entity_id,
                bot_id=bot_id,
                engine_type=engine_type,
            )
        else:
            self.skills_dir = SKILLS_DIR
            self.repo_dir = SKILLS_REPO_DIR
            self.local_dir = SKILLS_LOCAL_DIR

        self.skill_set_service = skill_set_factory.create(
            skills_dir=self.skills_dir, repo_dir=self.repo_dir, local_dir=self.local_dir,
            user_id=user_id, entity_id=entity_id, bot_id=bot_id, engine_type=engine_type
        )
        if isinstance(self.skill_set_service.skills_dir, Path):
            self.skills_dir = self.skill_set_service.skills_dir
            self.repo_dir = self.skill_set_service.repo_dir
            self.local_dir = self.skill_set_service.local_dir

    def _bot_layout_scope(self, user_id: str | None) -> BotSkillLayoutScope | None:
        """Resolve a Bot lock by owner, while retaining its entity as scope."""
        owner_id = (
            user_id
            or self.skill_set_service.user_id
            or self.skill_set_service.entity_id
        )
        bot_id = self.skill_set_service.bot_id
        if not owner_id or not bot_id:
            return None
        bot = self.skill_set_service._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None or bot.get("entity_id") is None or bot.get("env") is None:
            return None
        return BotSkillLayoutScope(
            env=str(bot["env"]),
            entity_id=str(bot["entity_id"]),
            bot_id=str(bot_id),
        )

    async def activate_skill_set(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        proxy_token: str | None = None
    ) -> ActivateResult:
        """Serialize Bot-scoped activation with Local Skill mutations."""
        scope = self._bot_layout_scope(user_id)
        if scope is None:
            return await self._activate_skill_set_unlocked(
                skill_set_id, user_id=user_id, proxy_token=proxy_token
            )
        try:
            lease = await self._edit_guard.acquire_for_edit_wait(scope=scope)
        except SkillsPoolEditPausedError:
            return ActivateResult(
                success=False,
                message="Skills are temporarily read-only while layout work is running",
            )
        try:
            return await self._activate_skill_set_unlocked(
                skill_set_id, user_id=user_id, proxy_token=proxy_token
            )
        finally:
            self._edit_guard.release(lease)

    async def _activate_skill_set_unlocked(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        proxy_token: str | None = None,
    ) -> ActivateResult:
        """激活单个能力集（增量激活）

        注意：
        - 不会清除其他已激活的能力集
        - 默认能力集通过关联表 ac_user_default_skill_set 控制启用状态
        """
        result = ActivateResult(success=False, message="")

        logger.info(f"[SkillSetActivator.activate] Starting activate: skill_set_id={skill_set_id}, user_id={user_id}, bot_id={self.skill_set_service.bot_id}")

        # 1. 验证能力集存在
        skill_set = self.skill_set_service.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            result.message = f"技能集 '{skill_set_id}' 不存在"
            logger.warning(f"[SkillSetActivator.activate] Skill set not found: {skill_set_id}")
            return result

        skill_set_name = skill_set.get('name', 'Unknown')
        is_default = skill_set.get('is_default', False)

        logger.info(f"[SkillSetActivator.activate] Found skill set: id={skill_set.get('id')}, name={skill_set_name}, is_default={is_default}")

        # 2. 默认能力集：ac_user_default_skill_set 已下线，default skill set 始终视为启用
        if is_default:
            result.success = True
            result.message = f"默认技能集 '{skill_set_name}' 已启用"
            logger.info("[SkillSetActivator.activate] Default skill set always enabled (ac_user_default_skill_set dropped)")
            return result
        else:
            # 3. 普通能力集：检查 is_active 状态
            if skill_set.get('is_active'):
                result.success = True
                result.message = f"技能集 '{skill_set_name}' 已经处于激活状态"
                logger.info("[SkillSetActivator.activate] Skill set already active")
                return result

        # 4. 记录要激活的技能（不再本地创建 symlink）
        logger.info("[SkillSetActivator.activate] Skipping local symlink operations (using proxy sync only)")
        skills = self.skill_set_service.get_set_skills(skill_set_id, user_id)
        result.activated = [skill.get('id') for skill in skills if skill.get('git_path')]
        logger.info(f"[SkillSetActivator.activate] Skills to activate: {len(result.activated)}")

        # 5. 更新数据库 is_active = 1
        self.skill_set_service.skill_set_repo.activate_skill_set(
            skill_set_id=skill_set_id,
            user_id=user_id,
            bolt_id=self.skill_set_service.bot_id
        )
        logger.info(f"[SkillSetActivator.activate] Set is_active=1 in DB for skill_set_id={skill_set_id}")

        # 6. 判断结果
        if len(result.failed) == 0:
            result.success = True
            result.message = f"成功激活技能集 '{skill_set_name}'，激活 {len(result.activated)} 个技能"
        else:
            result.success = True  # 部分成功
            result.message = f"激活技能集 '{skill_set_name}' 完成，激活 {len(result.activated)} 个技能，{len(result.failed)} 个失败"

        logger.info(f"[SkillSetActivator.activate] Completed: success={result.success}, message={result.message}")

        # 7. 更新 skill_sets.json（引擎依赖此文件加载技能）
        if result.success:
            SkillSetMetadataWriter(
                skill_set_repo=self.skill_set_service.skill_set_repo,
                skill_repo=self.skill_set_service.skill_repo,
                skills_dir=self.skills_dir,
                user_id=user_id,
                bot_id=self.skill_set_service.bot_id,
            ).write_metadata()

        # 8. 同步软链到远程设备（通过 DeviceSyncPlugin）
        if result.success:
            sync_result = self._do_device_sync(user_id, caller="SkillSetActivator.activate")
            if not sync_result.get("success"):
                # 软链同步失败，标记为部分成功并记录错误
                result.success = False
                result.message = f"技能集激活成功，但软链同步失败: {sync_result.get('error', 'Unknown error')}"
                result.failed.append({"error": "device_sync_failed", "message": sync_result.get("error", "Unknown error")})
                logger.error(f"[SkillSetActivator.activate] Device sync failed: {sync_result}")

        # 9. 触发轻量同步（filter-servers，更新 allowServers 列表）
        if result.success:
            scope_result = await self.skill_set_service.refresh_mcp_scope(user_id=user_id)
            if not scope_result.get("success"):
                error = scope_result.get("error", "Unknown error")
                result.success = False
                result.message = f"技能集激活成功，但 MCP 授权范围刷新失败: {error}"
                result.failed.append({"error": "mcp_scope_sync_failed", "message": error})
                logger.error("[SkillSetActivator.activate] 刷新 MCP 授权范围失败: %s", error)
            else:
                logger.info("[SkillSetActivator.activate] Light sync done")
                detail_result = await self.skill_set_service._mcp_sync_service.sync_mcp_details(
                    user_id=user_id,
                    entity_id=self.skill_set_service.entity_id,
                    bot_id=self.skill_set_service.bot_id,
                    entity_type=self.skill_set_service.entity_type,
                    engine_type=self.skill_set_service.engine_type,
                    active_only=True,
                )
                if not detail_result.get("success"):
                    error = detail_result.get("error", "Unknown error")
                    result.success = False
                    result.message = f"技能集激活成功，但 MCP 配置同步失败: {error}"
                    result.failed.append({"error": "mcp_detail_sync_failed", "message": error})
                    logger.error("[SkillSetActivator.activate] 激活 MCP 详情同步失败: %s", error)

        return result

    async def deactivate_skill_set(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        proxy_token: str | None = None
    ) -> DeactivateResult:
        """Serialize Bot-scoped deactivation with Local Skill mutations."""
        scope = self._bot_layout_scope(user_id)
        if scope is None:
            return await self._deactivate_skill_set_unlocked(
                skill_set_id, user_id=user_id, proxy_token=proxy_token
            )
        try:
            lease = await self._edit_guard.acquire_for_edit_wait(scope=scope)
        except SkillsPoolEditPausedError:
            return DeactivateResult(
                success=False,
                message="Skills are temporarily read-only while layout work is running",
            )
        try:
            return await self._deactivate_skill_set_unlocked(
                skill_set_id, user_id=user_id, proxy_token=proxy_token
            )
        finally:
            self._edit_guard.release(lease)

    async def _deactivate_skill_set_unlocked(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        proxy_token: str | None = None,
    ) -> DeactivateResult:
        """取消激活单个能力集

        默认能力集通过关联表 ac_user_default_skill_set 控制启用状态
        """
        result = DeactivateResult(success=False, message="")

        logger.info(f"[SkillSetActivator.deactivate] Starting deactivate: skill_set_id={skill_set_id}, user_id={user_id}, bot_id={self.skill_set_service.bot_id}")

        # 1. 验证能力集存在
        skill_set = self.skill_set_service.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            result.message = f"技能集 '{skill_set_id}' 不存在"
            logger.warning(f"[SkillSetActivator.deactivate] Skill set not found: {skill_set_id}")
            return result

        skill_set_name = skill_set.get('name', 'Unknown')
        is_default = skill_set.get('is_default', False)

        logger.info(f"[SkillSetActivator.deactivate] Found skill set: id={skill_set.get('id')}, name={skill_set_name}, is_default={is_default}")

        # 2. 默认能力集：ac_user_default_skill_set 已下线，default skill set 无法禁用（保持启用）
        if is_default:
            result.success = False
            result.message = f"默认技能集 '{skill_set_name}' 无法禁用，所有默认技能集保持启用状态"
            logger.info("[SkillSetActivator.deactivate] Default skill set cannot be disabled (ac_user_default_skill_set dropped)")
            return result
        else:
            # 3. 普通能力集：检查 is_active 状态
            if not skill_set.get('is_active'):
                result.success = True
                result.message = f"技能集 '{skill_set_name}' 已经处于未激活状态"
                logger.info("[SkillSetActivator.deactivate] Skill set already inactive")
                return result

        # 4. 记录要取消激活的技能（不再本地删除 symlink）
        logger.info("[SkillSetActivator.deactivate] Skipping local symlink operations (using proxy sync only)")
        skills = self.skill_set_service.get_set_skills(skill_set_id, user_id)
        result.deactivated = [skill.get('id') for skill in skills]
        logger.info(f"[SkillSetActivator.deactivate] Skills to deactivate: {len(result.deactivated)}")

        # 5. 更新数据库 is_active = 0
        try:
            self.skill_set_service.skill_set_repo.deactivate_skill_set(
                skill_set_id=skill_set_id,
                user_id=user_id,
                bolt_id=self.skill_set_service.bot_id
            )
            logger.info(f"[SkillSetActivator.deactivate] Set is_active=0 in DB for skill_set_id={skill_set_id}")
        except ValueError as e:
            result.message = str(e)
            logger.warning(f"[SkillSetActivator.deactivate] {e}")
            return result

        # 6. 判断结果
        if len(result.failed) == 0:
            result.success = True
            result.message = f"成功取消激活技能集 '{skill_set_name}'，取消激活 {len(result.deactivated)} 个技能"
        else:
            result.success = True  # 部分成功
            result.message = f"取消激活技能集 '{skill_set_name}' 完成，取消激活 {len(result.deactivated)} 个技能，{len(result.failed)} 个失败"

        logger.info(f"[SkillSetActivator.deactivate] Completed: success={result.success}, message={result.message}")

        # 7. 更新 skill_sets.json（引擎依赖此文件加载技能）
        if result.success:
            SkillSetMetadataWriter(
                skill_set_repo=self.skill_set_service.skill_set_repo,
                skill_repo=self.skill_set_service.skill_repo,
                skills_dir=self.skills_dir,
                user_id=user_id,
                bot_id=self.skill_set_service.bot_id,
            ).write_metadata()

        # 8. 同步软链到远程设备（通过 DeviceSyncPlugin）
        if result.success:
            sync_result = self._do_device_sync(user_id, caller="SkillSetActivator.deactivate")
            if not sync_result.get("success"):
                # 软链同步失败，标记为失败并记录错误
                result.success = False
                result.message = f"技能集取消激活成功，但软链同步失败: {sync_result.get('error', 'Unknown error')}"
                result.failed.append({"error": "device_sync_failed", "message": sync_result.get("error", "Unknown error")})
                logger.error(f"[SkillSetActivator.deactivate] Device sync failed: {sync_result}")

        # 9. 触发轻量同步（filter-servers，更新 allowServers 列表）
        if result.success:
            scope_result = await self.skill_set_service.refresh_mcp_scope(user_id=user_id)
            if not scope_result.get("success"):
                error = scope_result.get("error", "Unknown error")
                result.success = False
                result.message = f"技能集取消激活成功，但 MCP 授权范围刷新失败: {error}"
                result.failed.append({"error": "mcp_scope_sync_failed", "message": error})
                logger.error("[SkillSetActivator.deactivate] 刷新 MCP 授权范围失败: %s", error)
            else:
                logger.info("[SkillSetActivator.deactivate] Light sync done")
                detail_result = await self.skill_set_service._mcp_sync_service.sync_mcp_details(
                    user_id=user_id,
                    entity_id=self.skill_set_service.entity_id,
                    bot_id=self.skill_set_service.bot_id,
                    entity_type=self.skill_set_service.entity_type,
                    engine_type=self.skill_set_service.engine_type,
                    active_only=True,
                )
                if not detail_result.get("success"):
                    error = detail_result.get("error", "Unknown error")
                    result.success = False
                    result.message = f"技能集取消激活成功，但 MCP 配置同步失败: {error}"
                    result.failed.append({"error": "mcp_detail_sync_failed", "message": error})
                    logger.error("[SkillSetActivator.deactivate] MCP 配置同步失败: %s", error)

        return result
