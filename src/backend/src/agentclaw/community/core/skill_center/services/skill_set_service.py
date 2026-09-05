"""SkillSet service layer for managing capability sets.

Migrated from: services/openclawserver/server/services/skill_set_service.py
"""
import asyncio
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, List, Mapping, Optional

from agentclaw.community.core.devices.models import SynlinkMappingInfo
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor

if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.bot import BotRepository
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
    from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset
from agentclaw.community.core.mcp.services._defaults import (
    get_default_mcp_config,
    get_default_mcp_server_codes,
    get_default_mcp_servers,
)
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.repository.protocols.skill_center import SkillSetRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.services.skill_service import SkillService
from agentclaw.community.core.skill_center.policies.default_skill_set_selection import (
    DefaultSkillSetSelection,
    DefaultSkillSetSelectionPolicy,
)
from agentclaw.community.core.skill_center.path_resolution import (
    canonical_pool_local_path,
)
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeDesiredState,
    resolve_effective_mcp_server_codes,
)
from agentclaw.community.core.skill_center.utils.skill_metadata_writer import SkillSetMetadataWriter
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE  # noqa: E402
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
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
        runtime_engine_type: str | None = None,
        entity_type: str | None = None,
        resolver: "DeviceContextResolver | None" = None,
        device_sync_dispatcher: "DeviceSyncDispatcher | None" = None,
        mcp_sync_service=None,
        device_plugin: DeviceAccessor | None = None,
        ext_info_provider: Callable[[str], Mapping[str, Any] | None] | None = None,
        default_skill_set_selection_policy: DefaultSkillSetSelectionPolicy | None = None,
        *,
        path_factory: WorkspacePathFactory,
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

    def _default_skill_set_selection(
        self, engine_type: str | None = None
    ) -> DefaultSkillSetSelection:
        return self._default_skill_set_selection_candidates(engine_type)[0]

    def _default_skill_set_selection_candidates(
        self, engine_type: str | None = None, bolt_id: str | None = None
    ) -> tuple[DefaultSkillSetSelection, ...]:
        return self._default_skill_set_selection_policy.resolve_candidates(
            persisted_engine_type=self.engine_type if engine_type is None else engine_type,
            runtime_engine_type=self.runtime_engine_type,
            bolt_id=bolt_id,
        )

    def _default_skill_set_query_kwargs(
        self,
        engine_type: str | None = None,
        selection: DefaultSkillSetSelection | None = None,
    ) -> dict[str, str | None]:
        """Return repository kwargs only when compatibility needs them.

        OpenClaw and ordinary Claude Code keep the exact historical query shape.
        Routed Claude Code can provide ordered global default lookup candidates
        through the default SkillSet selection policy.
        """
        persisted_engine = self.engine_type if engine_type is None else engine_type
        effective_selection = selection or self._default_skill_set_selection(persisted_engine)
        if (
            effective_selection.bolt_id is None
            and effective_selection.engine_type == persisted_engine
        ):
            return {}
        return {
            "default_skill_set_bolt_id": effective_selection.bolt_id,
            "default_skill_set_engine_type": effective_selection.engine_type,
        }

    @staticmethod
    def _has_default_skill_set(skill_sets: list[dict]) -> bool:
        return any(skill_set.get("is_default") for skill_set in skill_sets)

    def _ordered_active_default_selections(
        self, *, bolt_id: str | None, engine_type: str | None
    ) -> tuple[DefaultSkillSetSelection, ...] | None:
        candidates = self._default_skill_set_selection_candidates(
            engine_type, bolt_id=bolt_id
        )
        if (
            len(candidates) == 1
            and candidates[0].bolt_id is None
            and candidates[0].engine_type == engine_type
        ):
            return None

        # Compatibility-only path. Keep the generic repository query primitive
        # unchanged, and let engine-specific resolvers contribute the lookup
        # order (for example: bot-scoped default -> global fallbacks).
        return candidates

    def _get_all_active_skill_sets_with_default_fallback(
        self,
        *,
        user_id: str | None,
        bolt_id: str | None,
        engine_type: str | None,
    ) -> list[dict]:
        selections = self._ordered_active_default_selections(
            bolt_id=bolt_id, engine_type=engine_type
        )
        if selections is None:
            return self.skill_set_repo.get_all_active_skill_sets(
                user_id=user_id,
                bolt_id=bolt_id,
                engine_type=engine_type,
            )

        first_result: list[dict] | None = None
        for selection in selections:
            result = self.skill_set_repo.get_all_active_skill_sets(
                user_id=user_id,
                bolt_id=bolt_id,
                engine_type=engine_type,
                **self._default_skill_set_query_kwargs(engine_type, selection),
            )
            if first_result is None:
                first_result = result
            if self._has_default_skill_set(result):
                return result
        return first_result or []

    def _get_all_active_skill_sets_for_env_with_default_fallback(
        self,
        *,
        user_id: str | None,
        bolt_id: str | None,
        engine_type: str | None,
        env: str,
    ) -> list[dict]:
        selections = self._ordered_active_default_selections(
            bolt_id=bolt_id, engine_type=engine_type
        )
        if selections is None:
            return self.skill_set_repo.get_all_active_skill_sets_for_env(
                user_id=user_id,
                bolt_id=bolt_id,
                engine_type=engine_type,
                env=env,
            )

        first_result: list[dict] | None = None
        for selection in selections:
            result = self.skill_set_repo.get_all_active_skill_sets_for_env(
                user_id=user_id,
                bolt_id=bolt_id,
                engine_type=engine_type,
                env=env,
                **self._default_skill_set_query_kwargs(engine_type, selection),
            )
            if first_result is None:
                first_result = result
            if self._has_default_skill_set(result):
                return result
        return first_result or []

    def _get_default_capabilities_ext_info(
        self,
        engine_type: Optional[str],
        bot_id: Optional[str] = None,
        *,
        strict: bool = False,
    ) -> Mapping[str, Any] | None:
        """Resolve extra context for engine-specific default capabilities.

        Display/read callers keep their historical best-effort behavior.
        Runtime projection passes ``strict=True`` because a dependency failure
        must not be interpreted as removal of template-only Default MCPs.
        """
        provider = self._ext_info_provider
        if provider is None:
            # Runtime DI always supplies this provider. In strict mode a
            # missing provider means the service was constructed incorrectly;
            # it is not evidence that this Bot has no template defaults.
            if strict:
                raise RuntimeError("Default MCP policy provider is unavailable")
            return None
        target_bot_id = bot_id or self.bot_id
        if not target_bot_id:
            return None
        if strict:
            ext_info = provider(str(target_bot_id))
        else:
            try:
                ext_info = provider(str(target_bot_id))
            except Exception as exc:
                logger.warning(
                    "[SkillSetService] default capabilities ext_info lookup failed for "
                    "engine_type=%s bot_id=%s: %s",
                    engine_type,
                    target_bot_id,
                    exc,
                )
                return None
        return ext_info if isinstance(ext_info, Mapping) else None

    def _get_default_capabilities_template_type(
        self,
        bot_id: Optional[str] = None,
        *,
        strict: bool = False,
    ) -> str | None:
        """Resolve template routing context for Default capabilities.

        Runtime projection uses strict mode for the same reason as ext info:
        a repository failure is not evidence that the Bot has no template.
        """
        target_bot_id = bot_id or self.bot_id
        if not target_bot_id or not self.entity_id:
            if strict:
                raise RuntimeError("Default MCP template context is unavailable")
            return None
        try:
            bot = self._bot_repo.get_by_id_and_owner(
                str(target_bot_id), str(self.entity_id)
            )
        except Exception as exc:
            if strict:
                raise
            logger.warning(
                "[SkillSetService] default capabilities template_type lookup failed for "
                "bot_id=%s: %s",
                target_bot_id,
                exc,
            )
            return None
        if not isinstance(bot, dict):
            if strict:
                raise RuntimeError("Default MCP template context is unavailable")
            return None
        template_type = bot.get("template_type")
        return template_type if isinstance(template_type, str) else None

    def _sync_symlinks_to_device_if_needed(
        self,
        user_id: Optional[str] = None,
        desired_skills: Optional[list[dict]] = None,
        effective_mcps: Optional[list[dict]] = None,
    ) -> bool:
        """如果需要，同步软链配置到设备。

        将当前激活技能集的软链配置同步到远程设备或本地文件系统。
        即使软链列表为空也会同步（空列表表示清空设备上的软链）。

        Args:
            user_id: 用户ID
            desired_skills: 调用方已解析的技能快照（可选）
            effective_mcps: 调用方已解析的 MCP 集合（可选）。仅整包投递的设备
                会用到它——它会跳过 compose 里重复的那次 DB 读取。

        Returns:
            True if sync attempted, False otherwise
        """
        try:
            # 获取当前激活技能集的软链配置（包括空列表，表示清空）
            mapping_kwargs = {"user_id": user_id, "bolt_id": self.bot_id}
            if desired_skills is not None:
                mapping_kwargs["desired_skills"] = desired_skills
            symlinks = self.get_symlink_mappings(**mapping_kwargs)

            # Resolve and invoke the provider-specific DeviceSync service.
            effective_user_id = user_id or self.entity_id or "default"
            ctx = self._resolver.resolve_for_bot(self.bot_id, effective_user_id)
            device_sync = self._device_sync_dispatcher.dispatch(ctx)

            logger.info(f"[_sync_symlinks_to_device_if_needed] Syncing {len(symlinks)} symlinks to device (bot_id={self.bot_id})")

            symlinks_dict = [sm.to_dict() for sm in symlinks]
            # Passed only when the caller actually resolved it, the same way
            # ``desired_skills`` is threaded above: the keyword means something
            # to a whole-artifact device and nothing to the rest, so a
            # DeviceSync implementation that has no use for it never has to
            # grow a parameter to stay callable from here.
            sync_kwargs: dict[str, Any] = {}
            if effective_mcps is not None:
                sync_kwargs["effective_mcps"] = effective_mcps
            if desired_skills is not None:
                sync_kwargs["desired_skills"] = desired_skills
            sync_result = device_sync.sync_symlinks(symlinks_dict, **sync_kwargs)

            if sync_result.get("success"):
                logger.info(f"[_sync_symlinks_to_device_if_needed] Sync successful: {sync_result.get('message')}")
                return True
            else:
                logger.error(f"[_sync_symlinks_to_device_if_needed] Sync failed: {sync_result.get('message')}")
                return False

        except Exception as e:
            logger.warning(f"[_sync_symlinks_to_device_if_needed] Failed to sync symlinks: {e}", exc_info=True)
            return False

    async def project_skills(
        self,
        *,
        desired_skills: Optional[list[dict]] = None,
        effective_mcps: Optional[list[dict]] = None,
    ) -> bool:
        """Apply one complete resolver-owned skill snapshot to the runtime.

        Async to match ``project_mcps``: the two halves of the capability
        boundary are one contract and should not differ in calling convention
        just because their internals do.

        The blocking work is dispatched here rather than by callers.
        ``_sync_symlinks_to_device_if_needed`` is synchronous and carries
        device resolution (including a blocking ws-info HTTP call), and on a
        whole-artifact engine a full artifact compose and the outbound apply
        request behind it. Owning the ``to_thread`` here makes staying off the
        event loop a property of this method, which no caller can forget —
        the same reason ``sync_mcp_desired_state`` wraps its own device calls.

        ``effective_mcps`` is the caller's already-resolved MCP set, carried
        for the same reason ``desired_skills`` is: on a whole-artifact engine
        the compose behind this call would otherwise re-read state the caller
        has just read. Meaningless to a device that consumes the symlinks
        directly, which is why it is optional and ignored there.
        """
        return await asyncio.to_thread(
            self._sync_symlinks_to_device_if_needed,
            self.user_id or self.entity_id,
            desired_skills,
            effective_mcps,
        )

    def _project_whole_artifact_sync(
        self,
        desired_skills: list[dict],
        effective_mcps: list[dict] | None,
    ) -> bool:
        """Deliver structured desired state without invoking path mapping."""
        try:
            effective_user_id = self.user_id or self.entity_id or "default"
            ctx = self._resolver.resolve_for_bot(self.bot_id, effective_user_id)
            device_sync = self._device_sync_dispatcher.dispatch(ctx)
            result = device_sync.sync_symlinks(
                [],
                desired_skills=desired_skills,
                effective_mcps=effective_mcps,
            )
            if not result.get("success"):
                raise SkillSetRuntimeReconcileError(
                    str(result.get("message") or "Skill set runtime sync failed")
                )
            return True
        except SkillSetRuntimeReconcileError:
            raise
        except Exception as exc:
            logger.warning(
                "[project_whole_artifact] delivery failed: %s", exc, exc_info=True
            )
            return False

    async def project_whole_artifact(
        self,
        *,
        desired_skills: list[dict],
        effective_mcps: list[dict] | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._project_whole_artifact_sync,
            desired_skills,
            effective_mcps,
        )

    async def project_mcps(
        self,
        *,
        claimed: frozenset[str],
        released: frozenset[str],
        declared: set[str],
    ) -> bool:
        """Apply one MCP projection to this Bot's device.

        The projector's single MCP entry point. It hands over what the
        mutation changed and what the Bot should end up holding; deciding how
        many device calls that takes, and in what order, belongs here, because
        this service — not the projector — owns device resolution.

        The order is the invariant, not an implementation detail:
        configuration lands before the allow-list cites it, and is withdrawn
        only after the allow-list stops covering it, so the device never
        references an MCP it has no configuration for.

        Both halves must run. A change that only releases still has to
        re-declare the smaller allow-list, and a change that only claims still
        has to declare the larger one — the declaration is a full replacement,
        so skipping it would leave the device's view of the set behind.
        """
        if not await self.sync_mcp_delivery(claimed=claimed, released=released):
            return False
        return await self.sync_mcp_desired_state(server_codes=declared)

    async def sync_mcp_delivery(
        self, *, claimed: frozenset[str], released: frozenset[str]
    ) -> bool:
        """Deliver configuration for newly claimed MCPs, withdraw it for released ones.

        The counterpart to ``sync_mcp_desired_state``: that one *declares* the
        complete allow-list, which is overwrite-style and must always carry
        the whole set. This one *delivers*, which is per-MCP and must only
        touch what actually changed — re-pushing an unchanged MCP rewrites its
        device-side configuration from the DB for no reason, and deleting one
        that is still supplied would break it.

        Both sets are declared by the mutation and already guarded against the
        projected set by the caller, so they are as small as the change was:
        one code for an MCP add or remove, the Set's members for an
        activation. ``sync_mcp_details_for_bot`` resolves the device once for
        the batch, so at one entry that is one device write, not a fan-out.
        """
        if not claimed and not released:
            return True
        try:
            entries: list[dict[str, Any]] = []
            for server_code in sorted(claimed):
                detail = self.mcp_center.get_mcp_detail(server_code)
                if not detail:
                    # Only ever a code we are actually installing — an
                    # unrelated catalogue gap cannot reach here and block the
                    # whole projection.
                    logger.error(
                        "[sync_mcp_delivery] no catalogue detail for %s, bot_id=%s",
                        server_code, self.bot_id,
                    )
                    return False
                entries.append(detail)
            if entries:
                logger.info(
                    "[sync_mcp_delivery] pushing MCP configuration: bot_id=%s, "
                    "mcps=%s, codes=%s",
                    self.bot_id, len(entries), sorted(claimed),
                )
                delivery = await self._mcp_sync_service.sync_mcp_details_for_bot(
                    user_id=self.user_id or self.entity_id or "",
                    mcp_entries=entries,
                    bot_id=self.bot_id,
                    entity_id=self.entity_id,
                    engine_type=self.engine_type,
                )
                if not delivery.get("success"):
                    logger.error(
                        "[sync_mcp_delivery] MCP configuration push failed: "
                        "bot_id=%s, error=%s",
                        self.bot_id, delivery.get("error"),
                    )
                    return False
            for server_code in sorted(released):
                # WARNING, not INFO: this deletes the MCP's stored endpoint,
                # api_key and headers from the device, and nothing here can
                # put them back.
                logger.warning(
                    "[sync_mcp_delivery] removing MCP configuration from device: "
                    "bot_id=%s, server_code=%s",
                    self.bot_id, server_code,
                )
                removal = await self._mcp_sync_service.remove_mcp_detail(
                    server_code=server_code,
                    bot_id=self.bot_id,
                    user_id=self.entity_id or self.user_id or "",
                )
                if not removal.get("success"):
                    logger.error(
                        "[sync_mcp_delivery] MCP removal failed: bot_id=%s, "
                        "server_code=%s, error=%s",
                        self.bot_id, server_code, removal.get("error"),
                    )
                    return False
            return True
        except Exception:
            logger.warning(
                "[sync_mcp_delivery] MCP delivery failed for bot_id=%s",
                self.bot_id,
                exc_info=True,
            )
            return False

    async def sync_mcp_desired_state(self, *, server_codes: set[str]) -> bool:
        """Declare the complete MCP allow-list to the Bot runtime.

        Declaration is total on purpose: ``sync_all_mcp_servers`` is the
        device-level reconciliation command and clears stale entries, so it
        runs even for an empty set.

        It is *only* declaration. Per-MCP configuration delivery is scoped to
        what a mutation actually changed and lives in ``sync_mcp_delivery``,
        which the caller runs first so configuration lands before the
        allow-list cites it. Folding the two together is what made every
        mutation re-push every MCP the Bot had.
        """
        try:
            # resolve_for_bot 与 sync_all_mcp_servers 都是同步阻塞调用(前者含
            # ws-info HTTP,后者是设备侧 HTTP),留在协程里会占住 event loop。
            ctx = await asyncio.to_thread(
                self._resolver.resolve_for_bot,
                self.bot_id,
                self.entity_id or self.user_id or "",
            )
            logger.info(
                "[sync_mcp_desired_state] declaring MCP allow-list: bot_id=%s, mcps=%s",
                self.bot_id, len(server_codes),
            )
            return bool(
                await asyncio.to_thread(
                    self._device_sync_dispatcher.dispatch(ctx).sync_all_mcp_servers,
                    # ``filter_servers`` reads server_code/serverCode off each
                    # entry, so bare strings would silently declare nothing.
                    [{"server_code": code} for code in sorted(server_codes)],
                )
            )
        except Exception:
            logger.warning(
                "[sync_mcp_desired_state] MCP allow-list declaration failed for bot_id=%s",
                self.bot_id,
                exc_info=True,
            )
            return False

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
        selections = self._ordered_active_default_selections(
            bolt_id=effective_bolt_id, engine_type=self.engine_type
        )
        if selections is None:
            return self.skill_set_repo.list_all(
                user_id,
                bolt_id=effective_bolt_id,
                engine_type=self.engine_type,
            )

        first_result: list[dict] | None = None
        for selection in selections:
            result = self.skill_set_repo.list_all(
                user_id=user_id,
                bolt_id=effective_bolt_id,
                engine_type=self.engine_type,
                **self._default_skill_set_query_kwargs(self.engine_type, selection),
            )
            if first_result is None:
                first_result = result
            if self._has_default_skill_set(result):
                return result
        return first_result or []

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

    def resolve_or_create_legacy_market_skill(
        self, *, identifier: str, owner_id: str, bot_id: str
    ) -> str:
        """Resolve the legacy name/path wire and materialize a Repo Skill if absent."""
        market_skill = next(
            (
                item
                for item in self.skill_service.get_skills_in_path("")
                if item.get("id") == identifier
                or str(item.get("path") or "").endswith(identifier)
            ),
            None,
        )
        if market_skill is None:
            raise ValueError("Skill not found")
        return str(self._create_skill_from_market(market_skill, owner_id, bot_id)["id"])

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
        # The Bot owner, matching how the exclusion is written and read.
        exclusion_owner_id = self.entity_id or user_id
        if skill_set.get('is_default') and exclusion_owner_id and self.bot_id:
            excluded = self.skill_set_repo.get_excluded_skills(
                user_id=exclusion_owner_id,
                bot_id=self.bot_id,
                skill_set_id=int(skill_set_id),
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
            engine_type=self.engine_type,
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
        # ``user_id`` is the caller in historical adapters. Installation and
        # Default exclusion state belong to the Bot owner carried by the
        # factory's entity_id, never to a collaborator acting on that Bot.
        effective_user_id = self.entity_id

        logger.info(f"[get_all_skill_sets_with_mcps] user_id={effective_user_id}, bolt_id={effective_bolt_id}")

        # 1. 获取所有能力集
        skill_sets = self.skill_set_repo.list_all(
            user_id=effective_user_id,
            bolt_id=effective_bolt_id,
            engine_type=self.engine_type,
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


    def list_active_skill_sets(
        self,
        *,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> list[dict]:
        """Return active SkillSets using the service-owned default lookup policy.

        This keeps delivery adapters from re-deriving default SkillSet
        compatibility kwargs while preserving historical query shape for engines
        that do not need a compatibility override.
        """
        effective_user_id = user_id if user_id is not None else self.entity_id
        effective_bolt_id = bolt_id if bolt_id is not None else self.bot_id
        effective_engine = engine_type if engine_type is not None else self.engine_type
        return self._get_all_active_skill_sets_with_default_fallback(
            user_id=effective_user_id,
            bolt_id=effective_bolt_id,
            engine_type=effective_engine,
        )

    def get_active_skills(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
    ) -> list[dict]:
        """The bot's active, de-duped skill DB records.

        Each record carries ``git_path`` — the source of truth for the skill's
        actual location (``git://<repo-rel>`` for shared market skills,
        ``local://<abs host path>`` for user uploads). Installation is the
        single source of truth: the reader flushes Set configuration
        (activation, Default membership, exclusions) into Installation, then
        answers from it alone.

        Used by both ``get_symlink_mappings`` (ARCA container symlinks) and the
        config-compose collector (teclaw — which reads ``git_path`` directly, no
        container round-trip); both consume only the published keys ``id``,
        ``name``, ``git_path``, ``skill_uuid``, ``sc_version_number``.
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id
        effective_user_id = user_id if user_id else self.entity_id
        if self._reader is None:
            raise RuntimeError(
                "SkillSetService.get_active_skills requires the capability "
                "state reader; construct through SkillSetServiceFactory"
            )
        try:
            assets = self._reader.active_skill_assets(
                bot_id=effective_bolt_id, owner_id=effective_user_id
            )
        except LocalSkillNotFoundError:
            # The legacy merge tolerated an unknown Bot by answering empty;
            # BFF display callers keep that grace.
            logger.warning(
                "[get_active_skills] Bot not found: user_id=%s, bolt_id=%s",
                effective_user_id,
                effective_bolt_id,
            )
            return []
        return [
            {
                "id": str(asset.skill_id),
                "name": asset.name,
                "git_path": asset.git_path,
                "skill_uuid": asset.skill_uuid,
                "sc_version_number": asset.sc_version_number,
            }
            for asset in assets
        ]

    # ====== Symlink Activation Config ======

    def get_symlink_mappings(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        additional_skill_paths: list[str] | None = None,
        desired_skills: list[dict] | None = None,
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
        unique_skills = (
            list(desired_skills)
            if desired_skills is not None
            else self.get_active_skills(user_id=user_id, bolt_id=bolt_id)
        )

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
            ENGINE_SKILLS_DIR_MAP.get(
                self.runtime_engine_type,
                "/home/admin/.openclaw/workspace/skills",
            )
        )
        # aicoding 引擎使用独立的 skills-repo 目录
        skills_repo_dir = Path(
            ENGINE_SKILLS_REPO_DIR_MAP.get(
                self.runtime_engine_type, str(base_skills_dir / "skills-repo")
            )
        )
        pool_layout_paths = None
        pool_owner_id = self.user_id or self.entity_id
        if pool_owner_id is not None:
            pool_layout_paths = self._pool_layout_paths(
                str(pool_owner_id),
                str(self.bot_id),
                self.runtime_engine_type,
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
                    self.runtime_engine_type,
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
                self.entity_id, self.bot_id, self.runtime_engine_type, base_skills_dir, skills_repo_dir,
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
                link_name = skill_name or (rel_path.split('/')[-1] if '/' in rel_path else rel_path)
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
            elif git_path.startswith('center://'):
                # This legacy file adapter has no Center request contract. A
                # caller must route such a projection through the mapping-v3
                # Engine adapter; silently omitting it would be fail-open.
                raise ValueError("center skill requires a Center-capable runtime adapter")
            else:
                raise ValueError("unsupported skill source in runtime projection")

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

    # ====== MCP Server reads (mutations live on the control plane) ======

    def get_set_mcp_servers(
        self,
        skill_set_id: str,
        user_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        engine_type: Optional[str] = None,
        template_type: Any = None,
        *,
        ext_info: Optional[Mapping[str, Any]] = None,
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
        effective_ext_info = (
            ext_info
            if ext_info is not None
            else self._get_default_capabilities_ext_info(effective_engine, bot_id)
        )
        effective_template_type = (
            template_type
            if template_type is not None
            else self._get_default_capabilities_template_type(bot_id)
        )

        # Get associations from DB (contains server_code and name)
        associations = self.skill_set_repo.get_mcp_servers_in_set(skill_set_id)

        # If this is a default skill set, merge with DEFAULT_MCP_SERVERS_CONFIG
        if skill_set.get('is_default'):
            default_codes = get_default_mcp_server_codes(
                effective_engine,
                effective_template_type,
                ext_info=effective_ext_info,
            )
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
                    default_cfg = get_default_mcp_config(
                        effective_engine,
                        code,
                        effective_template_type,
                        ext_info=effective_ext_info,
                    ) or {}
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
        *,
        strict_policy_context: bool = False,
    ) -> List[dict]:
        """Effective MCPs = default policy ∪ installed ∪ Skill dependencies.

        The Default half keeps its proven projection: static engine/template
        defaults plus Default-Set rows, minus this Bot's exclusions. Ordinary
        Sets no longer speak here — their members reach the Bot as
        Installation rows, which the reader answers after its lazy flush.
        Metadata for an installed code comes from the Bot's own Set membership
        rows when available, else a minimal entry (a direct installation has
        no membership row). No MCP Center round-trip.

        Args:
            engine_type: Engine type for scoping. Defaults to self.engine_type.
        """
        if self._reader is None:
            raise RuntimeError(
                "SkillSetService.collect_bot_active_mcps requires the "
                "capability state reader; construct through "
                "SkillSetServiceFactory"
            )
        effective_engine = engine_type if engine_type is not None else self.engine_type
        started_at = time.perf_counter()
        effective_ext_info = self._get_default_capabilities_ext_info(
            effective_engine,
            bot_id,
            strict=strict_policy_context,
        )
        self._log_effective_mcp_timing(
            stage="default_ext_info", bot_id=bot_id, engine_type=effective_engine,
            started_at=started_at, has_value=effective_ext_info is not None,
        )
        started_at = time.perf_counter()
        effective_template_type = self._get_default_capabilities_template_type(
            bot_id,
            strict=strict_policy_context,
        )
        self._log_effective_mcp_timing(
            stage="default_template_type", bot_id=bot_id, engine_type=effective_engine,
            started_at=started_at, has_value=effective_template_type is not None,
        )
        started_at = time.perf_counter()
        active_skill_sets = self._get_all_active_skill_sets_with_default_fallback(
            user_id=entity_id,
            bolt_id=bot_id,
            engine_type=effective_engine,
        )
        self._log_effective_mcp_timing(
            stage="active_skill_sets", bot_id=bot_id, engine_type=effective_engine,
            started_at=started_at, item_count=len(active_skill_sets),
        )

        # This Bot's exclusions silence a Default member entirely — the row
        # half too: ``get_set_mcp_servers`` filters only the static default
        # codes, and the flush already treats an exclusion as the Default
        # Set's per-Bot deactivation.
        started_at = time.perf_counter()
        excluded_codes = set(self.skill_set_repo.get_all_excluded_mcps(user_id, bot_id))
        self._log_effective_mcp_timing(
            stage="default_mcp_exclusions", bot_id=bot_id, engine_type=effective_engine,
            started_at=started_at, item_count=len(excluded_codes),
        )

        # Each phase appends entries and marks their codes in
        # ``seen_server_codes``, so a later phase never duplicates an earlier
        # one; ordering is the union's precedence (rows, policy, installed).
        active_mcps: List[dict] = []
        seen_server_codes: set = set()
        started_at = time.perf_counter()
        active_mcps.extend(
            self._default_set_mcp_rows(
                active_skill_sets,
                user_id=user_id,
                bot_id=bot_id,
                engine_type=effective_engine,
                template_type=effective_template_type,
                ext_info=effective_ext_info,
                excluded_codes=excluded_codes,
                seen_server_codes=seen_server_codes,
            )
        )
        self._log_effective_mcp_timing(
            stage="default_set_mcp_rows", bot_id=bot_id, engine_type=effective_engine,
            started_at=started_at, item_count=len(active_mcps),
        )
        started_at = time.perf_counter()
        active_mcps.extend(
            self._default_policy_mcp_entries(
                engine_type=effective_engine,
                template_type=effective_template_type,
                ext_info=effective_ext_info,
                excluded_codes=excluded_codes,
                seen_server_codes=seen_server_codes,
            )
        )
        self._log_effective_mcp_timing(
            stage="default_policy_mcp_entries", bot_id=bot_id,
            engine_type=effective_engine, started_at=started_at,
            item_count=len(active_mcps),
        )
        started_at = time.perf_counter()
        active_skill_assets = self._active_skill_assets(
            entity_id=entity_id, bot_id=bot_id, user_id=user_id
        )
        self._log_effective_mcp_timing(
            stage="active_skill_assets", bot_id=bot_id, engine_type=effective_engine,
            started_at=started_at, item_count=len(active_skill_assets),
        )
        started_at = time.perf_counter()
        installed_mcp_codes = self._installed_mcp_codes(
            entity_id=entity_id, bot_id=bot_id, user_id=user_id
        )
        self._log_effective_mcp_timing(
            stage="installed_mcp_codes", bot_id=bot_id, engine_type=effective_engine,
            started_at=started_at, item_count=len(installed_mcp_codes),
        )
        started_at = time.perf_counter()
        effective_non_default_codes = resolve_effective_mcp_server_codes(
            RuntimeDesiredState(
                skills=active_skill_assets,
                installed_mcp_server_codes=installed_mcp_codes,
            )
        )
        self._log_effective_mcp_timing(
            stage="resolve_non_default_codes", bot_id=bot_id,
            engine_type=effective_engine, started_at=started_at,
            item_count=len(effective_non_default_codes),
        )
        started_at = time.perf_counter()
        active_mcps.extend(
            self._non_default_effective_mcp_entries(
                effective_codes=effective_non_default_codes,
                active_skill_sets=active_skill_sets,
                seen_server_codes=seen_server_codes,
            )
        )
        self._log_effective_mcp_timing(
            stage="non_default_mcp_entries", bot_id=bot_id,
            engine_type=effective_engine, started_at=started_at,
            item_count=len(active_mcps),
        )

        logger.info(
            f"[collect_bot_active_mcps] bot_id={bot_id}, engine_type={effective_engine}, "
            f"total_mcps={len(active_mcps)}, codes={[m.get('server_code') for m in active_mcps]}"
        )
        return active_mcps

    @staticmethod
    def _log_effective_mcp_timing(
        *,
        stage: str,
        bot_id: str,
        engine_type: str | None,
        started_at: float,
        item_count: int | None = None,
        has_value: bool | None = None,
    ) -> None:
        logger.info(
            "[collect_bot_active_mcps] timing stage=%s bot_id=%s engine_type=%s "
            "duration_ms=%s item_count=%s has_value=%s",
            stage,
            bot_id,
            engine_type,
            round((time.perf_counter() - started_at) * 1000),
            item_count,
            has_value,
        )

    def _default_set_mcp_rows(
        self,
        active_skill_sets: List[dict],
        *,
        user_id: str,
        bot_id: str,
        engine_type: Optional[str],
        template_type: Optional[str],
        ext_info,
        excluded_codes: set,
        seen_server_codes: set,
    ) -> List[dict]:
        """The Default Set's association-row members, minus exclusions."""
        rows: List[dict] = []
        for skill_set in active_skill_sets:
            if not skill_set.get("is_default"):
                continue
            for mcp in self.get_set_mcp_servers(
                str(skill_set.get("id")),
                user_id,
                bot_id,
                engine_type,
                template_type,
                ext_info=ext_info,
            ):
                server_code = mcp.get("server_code")
                if (
                    server_code
                    and server_code not in excluded_codes
                    and server_code not in seen_server_codes
                ):
                    seen_server_codes.add(server_code)
                    rows.append(mcp)
        return rows

    def _default_policy_mcp_entries(
        self,
        *,
        engine_type: Optional[str],
        template_type: Optional[str],
        ext_info,
        excluded_codes: set,
        seen_server_codes: set,
    ) -> List[dict]:
        """The static engine/template default configs, minus exclusions."""
        entries: List[dict] = []
        for config in get_default_mcp_servers(
            engine_type,
            template_type,
            ext_info=ext_info,
        ):
            server_code = config["server_code"]
            if server_code in excluded_codes or server_code in seen_server_codes:
                continue  # Skip user-excluded default MCPs
            entry = {
                "server_code": server_code,
                "name": config.get("name") or server_code,
                "description": config.get("description", "Default MCP"),
                "status": "ONLINE",
            }
            if "icon" in config and config.get("icon"):
                entry["icon"] = config["icon"]
            if "headers" in config:
                entry["headers"] = config["headers"]
            seen_server_codes.add(server_code)
            entries.append(entry)
        return entries

    def _installed_mcp_codes(
        self, *, entity_id: str, bot_id: str, user_id: str
    ) -> frozenset:
        """The installed half, read through the reader (which flushes first)."""
        try:
            return self._reader.active_mcp_server_codes(
                bot_id=bot_id, owner_id=user_id
            )
        except LocalSkillNotFoundError:
            # Entity-keyed callers (the device-alive MCP sync, diagnostics)
            # land here when ``user_id`` is the Bot's entity, not its owner —
            # Installation is owner-keyed, so resolve the owner and retry
            # before giving the installed half up.
            bot = self._bot_repo.get_by_id_and_entity(bot_id, entity_id)
            owner = str(bot.get("owner_id") or "") if bot else ""
            if bot is not None and owner and owner != user_id:
                return self._reader.active_mcp_server_codes(
                    bot_id=bot_id, owner_id=owner, bot=bot
                )
            logger.warning(
                "[collect_bot_active_mcps] Bot not found: user_id=%s, "
                "bot_id=%s",
                user_id,
                bot_id,
            )
            return frozenset()

    def _active_skill_assets(
        self, *, entity_id: str, bot_id: str, user_id: str
    ) -> tuple["RegisteredSkillAsset", ...]:
        """Installed Skill assets whose declarations supply derived MCP codes."""
        try:
            return self._reader.active_skill_assets(
                bot_id=bot_id, owner_id=user_id
            )
        except LocalSkillNotFoundError:
            bot = self._bot_repo.get_by_id_and_entity(bot_id, entity_id)
            owner = str(bot.get("owner_id") or "") if bot else ""
            if bot is not None and owner and owner != user_id:
                return self._reader.active_skill_assets(
                    bot_id=bot_id, owner_id=owner, bot=bot
                )
            logger.warning(
                "[collect_bot_active_mcps] Bot not found while reading Skill "
                "dependencies: user_id=%s, bot_id=%s",
                user_id,
                bot_id,
            )
            return ()

    def _non_default_effective_mcp_entries(
        self,
        *,
        effective_codes,
        active_skill_sets: List[dict],
        seen_server_codes: set,
    ) -> List[dict]:
        """Non-default Effective codes, enriched from membership when possible.

        An ordinary Set's association row is the best metadata an installed
        code can have. Direct Installation and Skill dependency supply have no
        membership row, so their entries are minimal.
        """
        missing_codes = [
            code for code in sorted(effective_codes)
            if code not in seen_server_codes
        ]
        if not missing_codes:
            return []
        membership_metadata: dict[str, dict] = {}
        for skill_set in active_skill_sets:
            if skill_set.get("is_default"):
                continue
            for assoc in self.skill_set_repo.get_mcp_servers_in_set(
                str(skill_set.get("id"))
            ):
                code = assoc.get("server_code")
                if code and code not in membership_metadata:
                    membership_metadata[code] = assoc
        entries: List[dict] = []
        for code in missing_codes:
            assoc = membership_metadata.get(code)
            # One shape for every union entry, matching
            # ``get_set_mcp_servers``'s normalization.
            entries.append(
                {
                    "id": assoc.get("id") if assoc else None,
                    "server_code": code,
                    "name": (assoc.get("name") if assoc else None) or code,
                    "description": (
                        assoc.get("description") if assoc else None
                    ) or "",
                    "icon": assoc.get("icon") if assoc else None,
                    "status": "ONLINE",
                    "is_default": False,
                }
            )
            seen_server_codes.add(code)
        return entries

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
        effective_ext_info = self._get_default_capabilities_ext_info(
            effective_engine,
            bot_id,
        )
        effective_template_type = self._get_default_capabilities_template_type(bot_id)
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
            mcps_in_set = self.get_set_mcp_servers(
                skill_set_id,
                user_id,
                bot_id,
                effective_engine,
                effective_template_type,
                ext_info=effective_ext_info,
            )
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

        default_mcp_configs = get_default_mcp_servers(
            effective_engine,
            effective_template_type,
            ext_info=effective_ext_info,
        )
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
        active_skill_sets = self._get_all_active_skill_sets_for_env_with_default_fallback(
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
        effective_ext_info = self._get_default_capabilities_ext_info(
            effective_engine,
            bot_id,
        )
        effective_template_type = self._get_default_capabilities_template_type(bot_id)
        for default_mcp in get_default_mcp_servers(
            effective_engine,
            effective_template_type,
            ext_info=effective_ext_info,
        ):
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
        effective_ext_info = self._get_default_capabilities_ext_info(
            effective_engine,
            bot_id,
        )
        effective_template_type = self._get_default_capabilities_template_type(bot_id)
        active_skill_sets = self._get_all_active_skill_sets_with_default_fallback(
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
            mcps_in_set = self.get_set_mcp_servers(
                skill_set_id,
                user_id,
                bot_id,
                effective_engine,
                effective_template_type,
                ext_info=effective_ext_info,
            )
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
