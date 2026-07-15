"""MCP 同步编排服务。

按职责拆分为配置推送（sync_mcp_details / sync_mcp_detail / remove_mcp_detail）
与权限刷新（refresh_mcp_scope）两个层面。
``MCPSyncService`` 只依赖 Protocol，无需旧单体服务即可测试。
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from injector import inject

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.services.device_context import (
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.mcp.services._defaults import get_default_cli_items
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.mcp.services.passport_scope import (
    passport_mcp_codes_from_entries,
    passport_mcp_items_from_entries,
)
from agentclaw.community.core.mcp.services.repositories import BotMCPProvider, UserMCPConfigRepository
from agentclaw.community.plugin_api.device_sync import DeviceSyncPlugin
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.plugin_api.passport import CliItem, PassportPlugin
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.devices.services.device_sync_dispatcher import DeviceSyncDispatcher

logger = get_logger()


def _merge_cli_items(
    current: list[CliItem] | None,
    defaults: list[CliItem] | None,
) -> list[CliItem]:
    """Merge passport CLI scope with default CLI items, de-duped by cli_code.

    The passport update API treats resourceManifest as an overwrite. During MCP
    sync we must send the complete CLI scope as well as MCPs. If the passport
    service returns a temporarily-empty CLI list right after bot creation,
    preserving the engine defaults here prevents a later MCP sync from clearing
    them. Existing passport values win on duplicate cli_code so user/provider
    metadata is not overwritten by static defaults.
    """
    merged: list[CliItem] = []
    seen: set[str] = set()
    for item in (current or []) + (defaults or []):
        if not isinstance(item, dict):
            continue
        cli_code = item.get("cli_code")
        if not cli_code or cli_code in seen:
            continue
        seen.add(cli_code)
        merged.append(dict(item))
    return merged


@dataclass
class DeviceSyncResult:
    """设备 MCP 同步操作的结构化结果。"""

    success: bool = False
    stage: str = "init"
    total: int = 0
    success_count: int = 0
    failed_count: int = 0
    failed_server_codes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    retry_attempts: int = 0
    device_desc: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """展平为调用方期望的遗留字典格式。"""
        return {
            "success": self.success,
            "stage": self.stage,
            "total": self.total,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "failed_mcps": self.failed_server_codes,
            "errors": self.errors,
            "retry_attempts": self.retry_attempts,
            "device_ip": self.device_desc,
        }


class MCPSyncService:
    """编排批量 MCP 同步到设备以及 passport 更新。"""

    @inject
    def __init__(
        self,
        mcp_provider_factory: Callable[[], BotMCPProvider],
        mcp_center: MCPCenterPlugin,
        user_mcp_config_repo: UserMCPConfigRepository,
        passport_update: PassportPlugin,
        mcp_config_service: MCPConfigService,
        bot_repository: BotRepository,
        resolver_provider: 'Callable[[], "DeviceContextResolver"]',
        device_sync_dispatcher_provider: 'Callable[[], "DeviceSyncDispatcher"]',
    ) -> None:
        """初始化 MCP 同步编排服务。

        Args:
            mcp_provider_factory: 惰性工厂，用于延迟解析 ``BotMCPProvider``。
                直接注入会导致循环依赖，因此通过工厂在首次使用时再解析。
            mcp_center: MCP Center 插件，用于补全 MCP 元数据。
            user_mcp_config_repo: 用户 MCP 配置仓库。
            passport_update: Passport 插件，用于更新 passport MCP 列表。
            mcp_config_service: MCP 配置服务，用于构建同步请求参数。
            bot_repository: Bot 仓库，用于查询 bot 信息。
            resolver_provider: Lazy thunk 返回 ``DeviceContextResolver`` — 全仓
                唯一 provider 解析点。以 ``(bot_id, user_id)`` 入参,经 binding +
                ConnInfoBuilder 输出 typed ``DeviceContext``,取代旧
                ``DeviceSyncPluginSupplier`` 闭包。caller 无需再传 ``engine_type``
                (``ctx.conn_info`` 已含)。Lazy 是为了打破构造期 DI 循环:
                ``BotService → SkillSetServiceFactory → MCPSyncService
                → DeviceContextResolver → ArcaConnInfoBuilder → DeviceService
                → BotService`` —— 与 ``SkillSetServiceFactory`` 同款手法。
            device_sync_dispatcher_provider: Lazy thunk 返回 ``DeviceSyncDispatcher``
                — 按 ``ctx.provider`` 选 ``DeviceSyncPlugin`` 实例(arca/baas/teclaw)。
                MCP 投递不再分支 ``device_provider``;插件按容器类型决定投递方式
                (arca/baas 单条 ``/api/mcp`` 增量,teclaw 重组并投递整份
                ``BotConfigArtifact``)。Lazy 与 ``resolver_provider`` 同因。
        """
        self._mcp_provider_factory = mcp_provider_factory
        self._mcp_provider_cached: BotMCPProvider | None = None
        self.mcp_center = mcp_center
        self.user_mcp_config_repo = user_mcp_config_repo
        self.passport_update = passport_update
        self.mcp_config_service = mcp_config_service
        self.bot_repository = bot_repository
        self._resolver_provider = resolver_provider
        self._device_sync_dispatcher_provider = device_sync_dispatcher_provider

    @property
    def mcp_provider(self) -> BotMCPProvider:
        """获取 ``BotMCPProvider`` 实例（记忆化）。

        首次访问时通过惰性工厂解析并缓存，后续调用直接复用，
        避免单次同步流程中重复触发注入器查找。
        """
        if self._mcp_provider_cached is None:
            self._mcp_provider_cached = self._mcp_provider_factory()
        return self._mcp_provider_cached

    # ------------------------------------------------------------------
    # 配置层面 —— 推送 MCP 详细配置到设备
    # ------------------------------------------------------------------
    async def sync_mcp_details(
        self,
        user_id: str,
        entity_id: str,
        bot_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
        active_only: bool = False,
    ) -> dict[str, Any]:
        """推送 bot 关联的 MCP 完整配置到设备。

        Args:
            user_id: 用户 ID。
            entity_id: 实体 ID。
            bot_id: 目标 bot ID。
            entity_type: 实体类型，默认 ``staff``。
            engine_type: 引擎类型，默认 ``openclaw``。
            active_only: 为 True 时只推送当前**激活** skill sets 中的 MCP；
                为 False 时推送该 bot 关联的**全部** MCP（含 inactive）。

        Returns:
            同步结果字典，包含 ``success``、``success_count``、
            ``failed_count``、``failed_server_codes``、``synced_server_codes`` 等字段。
            设备离线时返回 ``{"success": False, "error": ...}``。
        """
        effective_engine = engine_type or "openclaw"
        scope_desc = "激活" if active_only else "全部"
        logger.info(
            "[MCPSyncService] 推送%s MCP 配置: user_id=%s, entity_id=%s, bot_id=%s, "
            "entity_type=%s, engine_type=%s",
            scope_desc, user_id, entity_id, bot_id, entity_type, effective_engine,
        )

        try:
            successes, failures = await self._sync_mcp_details(
                bot_id=bot_id,
                user_id=user_id,
                entity_id=entity_id,
                entity_type=entity_type,
                engine_type=effective_engine,
                active_only=active_only,
            )
        except RuntimeError as e:
            logger.error("[MCPSyncService] %s", e)
            return {"success": False, "error": str(e)}

        failed_codes = [
            (m.get("server_code") or m.get("serverCode") or "unknown")
            for m in failures
        ]
        synced_codes = [
            (m.get("server_code") or m.get("serverCode"))
            for m in successes
        ]

        result = DeviceSyncResult(
            success=len(failures) == 0,
            stage="complete" if not failures else "sync-config",
            total=len(successes) + len(failures),
            success_count=len(successes),
            failed_count=len(failures),
            failed_server_codes=failed_codes,
            errors=[f"{len(failures)} 个 MCP 同步失败: {', '.join(failed_codes)}"] if failures else [],
        ).to_dict()
        result["synced_server_codes"] = synced_codes

        logger.info(
            "[MCPSyncService] %s配置推送完成: bot_id=%s, 成功=%s, 失败=%s, synced=%s, failed=%s",
            scope_desc, bot_id, len(successes), len(failures), synced_codes, failed_codes,
        )
        return result

    async def sync_mcp_detail(
        self,
        *,
        user_id: str,
        mcp_data: dict[str, Any],
        bot_id: str,
        entity_id: Optional[str] = None,
        engine_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """将单个 MCP 配置推送到指定 bot 的设备。

        用户向某个 skill set 新增单个 MCP 时调用，推送到目标 bot。

        Args:
            user_id: 用户 ID。仅用于 ``build_mcp_sync_payload`` 取该用户的 MCP 配置
                （api_key 等），是 per-user 维度。
            mcp_data: MCP 配置数据，需包含 ``server_code``。
            bot_id: 目标 bot ID。
            entity_id: bot 所属实体 ID。用于取 per-bot 投递插件与（teclaw）整产物
                compose 的 entity 口径——与 ``_declare_mcp_scope`` / ``_sync_mcp_details``
                一致。缺省回退到 ``user_id``（个人 bot 二者相等）。
            engine_type: 引擎类型，默认 ``openclaw``。

        Returns:
            ``{"success": bool, "error": str|None}`` 格式的结果字典。
        """
        _bot = bot_id or "unknown"
        try:
            ctx = self._resolver_provider().resolve_for_bot(bot_id, entity_id or user_id)
        except (DeviceNotBoundError, UnknownProviderError):
            error = f"bot={_bot} 缺少设备连接信息，无法推送 MCP 配置"
            logger.error("[MCPSyncService] %s", error)
            return {"success": False, "error": error}
        plugin = self._device_sync_dispatcher_provider().dispatch(ctx)

        # 投递由 per-bot 插件按容器类型自行决定：arca/baas 走单条 /api/mcp 增量，
        # teclaw 重组并投递整份 artifact，local no-op——MCPSyncService 不再分支。
        sync_success = await self._sync_mcp_detail(
            plugin=plugin,
            user_id=user_id,
            mcp_data=mcp_data,
            engine_type=engine_type,
        )
        if not sync_success:
            error = f"向 bot={_bot} 推送 MCP 配置失败"
            logger.error("[MCPSyncService] %s", error)
            return {"success": False, "error": error}

        return {"success": True}

    async def remove_mcp_detail(
        self,
        *,
        server_code: str,
        bot_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """从指定 bot 的设备上移除单个 MCP。

        当 skill set 被取消激活或 MCP 被删除时调用，确保设备端不再
        持有该 MCP 的注册信息。

        Args:
            server_code: 要移除的 MCP server code。
            bot_id: 目标 bot ID。
            user_id: 用户 ID。

        Returns:
            ``{"success": bool, "error": str|None}`` 格式的结果字典。
        """
        _bot = bot_id or "unknown"
        try:
            ctx = self._resolver_provider().resolve_for_bot(bot_id, user_id)
        except (DeviceNotBoundError, UnknownProviderError):
            error = f"bot={_bot} 缺少设备连接信息，无法移除 MCP {server_code}"
            logger.error("[MCPSyncService] %s", error)
            return {"success": False, "error": error}
        plugin = self._device_sync_dispatcher_provider().dispatch(ctx)

        # teclaw 在 sync_remove_mcp 内部重组并投递整产物（compose 反映删除后的状态），
        # arca/baas 走单条移除请求——由插件按容器类型决定。
        ok = await asyncio.to_thread(plugin.sync_remove_mcp, server_code)
        if not ok:
            error = f"从 bot={_bot} 移除 MCP {server_code} 失败"
            logger.error("[MCPSyncService] %s", error)
            return {"success": False, "error": error}

        return {"success": True}

    # ------------------------------------------------------------------
    # 权限层面 —— 刷新白名单与许可证
    # ------------------------------------------------------------------
    async def refresh_mcp_scope(
        self,
        user_id: str,
        entity_id: str,
        bot_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """刷新 bot 的 MCP 授权范围。

        向设备声明允许访问的 MCP 白名单（filter-servers），并同步更新
        passport 中的 MCP codes 列表。skill set 切换、激活/取消激活后调用。

        Args:
            user_id: 用户 ID。
            entity_id: 实体 ID。
            bot_id: 目标 bot ID。
            entity_type: 实体类型，默认 ``staff``。
            engine_type: 引擎类型，默认 ``openclaw``。

        Returns:
            ``{"success": bool, "error": str|None}`` 格式的结果字典。
        """
        effective_engine = engine_type or "openclaw"
        logger.info(
            "[MCPSyncService] 刷新 MCP 授权范围: user_id=%s, entity_id=%s, bot_id=%s, "
            "entity_type=%s, engine_type=%s",
            user_id, entity_id, bot_id, entity_type, effective_engine,
        )

        # 收集当前 bot 下所有激活 skill set 中的 MCP，用于后续白名单声明和 passport 更新。
        active_mcps = self.mcp_provider.collect_bot_active_mcps(
            entity_id=entity_id,
            bot_id=bot_id,
            user_id=user_id,
            entity_type=entity_type,
            engine_type=effective_engine,
        )
        mcp_codes_list = [
            (m.get("server_code") or m.get("serverCode"))
            for m in active_mcps
            if m.get("server_code") or m.get("serverCode")
        ]
        passport_mcp_codes = passport_mcp_codes_from_entries(active_mcps)
        logger.info(
            "[MCPSyncService] 收集到激活 MCP codes: %s, bot_id=%s",
            mcp_codes_list, bot_id,
        )
        if passport_mcp_codes != mcp_codes_list:
            logger.info(
                "[MCPSyncService] 过滤 local MCP 后的 passport MCP codes: %s, bot_id=%s",
                passport_mcp_codes, bot_id,
            )

        # 先向设备声明白名单：即使 active_mcps 为空也会调用，防止设备残留旧白名单。
        scope_result = await self._declare_mcp_scope(
            bot_id=bot_id,
            user_id=user_id,
            entity_id=entity_id,
            entity_type=entity_type,
            engine_type=effective_engine,
        )
        if not scope_result.get("success"):
            return scope_result

        # 白名单声明成功后，再更新 passport 供前端权限校验使用。
        passport_result = await self._update_passport(
            bot_id=bot_id,
            user_id=user_id,
            synced_server_codes=passport_mcp_codes,
            engine_type=effective_engine,
        )
        if not passport_result.get("success"):
            return passport_result

        return {"success": True}

    async def sync_mcp_identity_to_agent_principal(
        self,
        *,
        user_id: str,
        entity_id: str,
        bot_id: str,
        entity_type: str,
        engine_type: str,
        active_mcps: list[dict[str, Any]],
        identity_modes: Mapping[str, object],
    ) -> dict[str, Any]:
        """Replace Agent Principal MCP identity metadata without device sync."""
        del entity_id, entity_type, engine_type
        try:
            mcp_items = passport_mcp_items_from_entries(
                active_mcps,
                identity_modes=identity_modes,
            )
            self.passport_update.update_mcp_identity_to_agent_principal(
                bot_id=bot_id,
                user_id=user_id,
                mcp_items=mcp_items,
            )
        except Exception as exc:
            logger.warning(
                "caller_agent_principal_sync_failed bot_id=%s error_type=%s",
                bot_id,
                type(exc).__name__,
            )
            return {"success": False, "error": "Agent Principal update failed"}

        logger.info(
            "caller_agent_principal_sync_succeeded bot_id=%s mcp_count=%s",
            bot_id,
            len(mcp_items),
        )
        return {"success": True}

    async def sync_mcp_detail_to_all_bots(
        self,
        *,
        user_id: str,
        server_code: str,
        mcp_data: dict[str, Any],
        entity_id: str,
        entity_type: str,
        api_key: Optional[str] = None,
        custom_headers: Optional[dict[str, str]] = None,
        endpoint_env: Optional[str] = None,
        transport_protocol: Optional[str] = None,
    ) -> dict[str, Any]:
        """将单个 MCP 配置推送到指定实体下的全部 bot。

        流程：列出实体下所有 bot → 探测设备是否已安装该 MCP →
        仅对已安装的设备执行同步。未安装或离线的 bot 会被记录原因并跳过。
        """
        bot_ids: list[str] = []
        try:
            total, bots = self.bot_repository.list_by_entity(
                entity_id=entity_id, entity_type=entity_type, page=1, page_size=100
            )
            logger.info(
                "[MCPSyncService] 查询实体 bot: entity=%s type=%s total=%s",
                entity_id, entity_type, total,
            )
            bot_ids = [bot["bot_id"] for bot in bots]
        except Exception as e:
            logger.error("[MCPSyncService] 查询 bot 列表失败: %s", e)

        if not bot_ids:
            logger.info("[MCPSyncService] 未找到 bot，无需同步")
            return {"success": True, "sync_results": [], "error": None}

        sync_results: list[dict[str, Any]] = []
        any_success = False
        has_mcp_devices = 0

        for bot_id in bot_ids:
            # per-bot 投递插件由 resolver+dispatcher 按容器类型路由；无可投递设备→跳过(不计入回滚判定)。
            try:
                ctx = self._resolver_provider().resolve_for_bot(bot_id, entity_id)
            except (DeviceNotBoundError, UnknownProviderError):
                logger.warning("[MCPSyncService] 跳过 bot=%s: 缺少连接信息", bot_id)
                sync_results.append({
                    "bot_id": bot_id, "synced": False, "reason": "缺少设备连接信息",
                })
                continue
            plugin = self._device_sync_dispatcher_provider().dispatch(ctx)

            try:
                # 探测设备是否已装该 MCP；arca/baas 真实探测，未装则跳过（不计入）。
                # 整产物设备（teclaw）的 has_mcp 恒为 True：始终投递并计入回滚判定，
                # 即一次 teclaw 投递失败会让本批整体回滚——与 arca/baas 一致（Option B）。
                # TODO(totalfrank): 与 teclaw 团队同步——配置投递失败会回滚用户的 MCP
                #   配置改动（容器无后台 re-pull，不能让已落库的改动停留在过期容器上）。
                #   凭据已内联进产物，改 api_key 即改产物字节，无需容器侧 secret broker
                #   或 auth_ref 二次解析。
                has_mcp = await asyncio.to_thread(plugin.has_mcp, server_code)
                if not has_mcp:
                    logger.warning(
                        "[MCPSyncService] bot=%s 设备上未找到 MCP %s", bot_id, server_code
                    )
                    sync_results.append({
                        "bot_id": bot_id, "synced": False, "reason": "设备上未找到该 MCP",
                    })
                    continue

                has_mcp_devices += 1
                logger.info("[MCPSyncService] 正在同步 MCP %s 到 bot=%s", server_code, bot_id)

                sync_success = await self._sync_mcp_detail(
                    plugin=plugin,
                    user_id=user_id,
                    mcp_data=mcp_data,
                    api_key=api_key,
                    custom_headers=custom_headers,
                    endpoint_env=endpoint_env,
                    transport_protocol=transport_protocol,
                )

                if sync_success:
                    logger.info("[MCPSyncService] bot=%s 同步成功", bot_id)
                    any_success = True
                else:
                    logger.error("[MCPSyncService] bot=%s 同步失败", bot_id)

                sync_results.append({
                    "bot_id": bot_id,
                    "synced": sync_success,
                    "error": None if sync_success else "设备同步返回失败",
                })
            except Exception as e:
                logger.error("[MCPSyncService] 同步到 bot=%s 异常: %s", bot_id, e)
                sync_results.append({
                    "bot_id": bot_id, "synced": False, "error": str(e),
                })

        # 只有"确实有该 MCP 的设备全部失败"时才整体报错；
        # 如果设备上没有该 MCP 或者根本没有设备，不算失败。
        if has_mcp_devices > 0 and not any_success:
            logger.error(
                "[MCPSyncService] 全部 %s 台含该 MCP 的设备均同步失败", has_mcp_devices
            )
            first_error = next(
                (
                    r.get("error")
                    for r in sync_results
                    if r.get("error")
                ),
                None,
            )
            return {
                "success": False,
                "sync_results": sync_results,
                "error": first_error or "所有设备同步失败",
            }

        return {"success": True, "sync_results": sync_results, "error": None}

    async def _declare_mcp_scope(
        self,
        bot_id: str,
        user_id: str,
        entity_id: str,
        entity_type: str,
        engine_type: str,
    ) -> dict[str, Any]:
        """向设备声明允许访问的 MCP 白名单（filter-servers）。

        即使 active_mcps 为空也会调用，防止设备残留旧白名单。

        Returns:
            ``{"success": bool, "error": str|None}`` 格式的结果字典。
        """
        _bot = bot_id or "unknown"
        # 用 entity_id 取 per-bot 投递插件：compose 按 entity 收集 bot 配置，与本方法
        # collect_bot_active_mcps 的 entity 口径一致。teclaw 的白名单(filter-servers)
        # 已含在整产物里，sync_all_mcp_servers 内部重组并投递整份 artifact 即可；
        # arca/baas 走 filter-servers 声明——由插件按容器类型决定。
        try:
            ctx = self._resolver_provider().resolve_for_bot(bot_id, entity_id)
        except (DeviceNotBoundError, UnknownProviderError):
            error = f"bot={_bot} 缺少设备连接信息，无法声明 MCP 白名单"
            logger.error("[MCPSyncService] %s", error)
            return {"success": False, "error": error}
        plugin = self._device_sync_dispatcher_provider().dispatch(ctx)

        active_mcps = self.mcp_provider.collect_bot_active_mcps(
            entity_id=entity_id,
            bot_id=bot_id,
            user_id=user_id,
            entity_type=entity_type,
            engine_type=engine_type,
        )
        mcp_codes_list = [
            (m.get("server_code") or m.get("serverCode"))
            for m in active_mcps
            if m.get("server_code") or m.get("serverCode")
        ]

        logger.info(
            "[MCPSyncService] 向 bot=%s 声明 MCP 白名单 (%s 个), codes=%s, "
            "entity_id=%s, user_id=%s",
            bot_id, len(active_mcps), mcp_codes_list, entity_id, user_id,
        )

        ok = await asyncio.to_thread(plugin.sync_all_mcp_servers, active_mcps)
        if not ok:
            error = f"向 bot={_bot} 声明 MCP 白名单失败"
            logger.error("[MCPSyncService] %s, codes=%s", error, mcp_codes_list)
            return {"success": False, "error": error}

        return {"success": True}

    async def _sync_mcp_details(
        self,
        bot_id: str,
        user_id: str,
        entity_id: str,
        entity_type: str,
        engine_type: str,
        active_only: bool = False,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """推送 MCP 完整配置到设备。

        Args:
            active_only: 为 True 时只推送当前**激活** skill sets 中的 MCP；
                为 False 时推送该 bot 关联的**全部** MCP（含 inactive）。

        返回 (successes, failures)。
        不重试——per-bot DeviceSyncPlugin 的 MCP 投递内部已做 HTTP 重试（3 次）。
        """
        # 1. 取 per-bot 投递插件；无可投递设备时抛异常，阻断整个详情同步流程。
        #    用 entity_id（与下方 collect_bot_*mcps 的 entity 口径一致）。投递走插件：
        #    arca/baas 逐条 sync_single_mcp；teclaw 的 sync_single_mcp 重组并投递整产物
        #    （逐条调用会重复投递同一整产物，幂等但冗余——可接受；_declare_mcp_scope 已覆盖
        #    空列表场景的 teclaw 投递）。
        _bot = bot_id or "unknown"
        try:
            ctx = self._resolver_provider().resolve_for_bot(bot_id, entity_id)
        except (DeviceNotBoundError, UnknownProviderError) as e:
            logger.error(
                "[MCPSyncService] bot=%s 无连接信息，无法推送 MCP 详情: %s",
                _bot, e,
            )
            raise RuntimeError(
                f"bot={_bot} 缺少设备连接信息，无法推送 MCP 配置"
            )
        plugin = self._device_sync_dispatcher_provider().dispatch(ctx)

        # 2. 收集 MCP：active_only 时只取激活 skill sets + 默认 MCP。
        if active_only:
            all_mcps = self.mcp_provider.collect_bot_active_mcps(
                entity_id=entity_id,
                bot_id=bot_id,
                user_id=user_id,
                entity_type=entity_type,
                engine_type=engine_type,
            )
        else:
            all_mcps = self.mcp_provider.collect_bot_mcps(
                entity_id=entity_id,
                bot_id=bot_id,
                user_id=user_id,
                entity_type=entity_type,
                engine_type=engine_type,
            )
        self._enrich_from_mcp_center(all_mcps)

        mcp_codes_list = [
            (m.get("server_code") or m.get("serverCode"))
            for m in all_mcps
            if m.get("server_code") or m.get("serverCode")
        ]
        logger.info(
            "[MCPSyncService] 正在推送 %s 个 MCP 详情到 bot=%s, codes=%s, "
            "entity_id=%s, user_id=%s",
            len(all_mcps), bot_id, mcp_codes_list, entity_id, user_id,
        )

        # 3. 并发推送，但限制并发数为 5，防止一次性向设备发太多 HTTP 请求把引擎压垮。
        semaphore = asyncio.Semaphore(5)

        async def sync_one(mcp: dict[str, Any]) -> tuple[dict[str, Any], bool]:
            async with semaphore:
                server_code = mcp.get("server_code") or mcp.get("serverCode")
                if not server_code:
                    logger.warning(
                        "[MCPSyncService] 跳过无 server_code 的 MCP, "
                        "bot_id=%s, mcp=%s",
                        bot_id, mcp,
                    )
                    return (mcp, False)
                try:
                    # 经共享 helper 合并 payload 并下发；插件按容器类型决定投递方式
                    # （arca/baas 单条 /api/mcp；teclaw 整产物）。
                    ok = await self._sync_mcp_detail(
                        plugin=plugin,
                        user_id=user_id,
                        mcp_data=mcp,
                        engine_type=engine_type,
                    )
                    return (mcp, ok)
                except Exception as e:
                    logger.error(
                        "[MCPSyncService] 同步 %s 异常, bot_id=%s, error=%s",
                        server_code, bot_id, e,
                    )
                    return (mcp, False)

        tasks = [sync_one(mcp) for mcp in all_mcps]
        # return_exceptions=True 保证单个 MCP 失败不会阻断其他 MCP 的同步。
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 4. 分类收集成功和失败的 MCP；CancelledError 必须向上抛，不能吞掉。
        successes: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []

        for mcp, res in zip(all_mcps, results, strict=True):
            if isinstance(res, asyncio.CancelledError):
                # CancelledError 代表上层要求取消任务，必须向上抛，不能吞掉。
                raise res
            if isinstance(res, Exception):
                server_code = mcp.get("server_code") or mcp.get("serverCode")
                logger.error(
                    "[MCPSyncService] 任务异常 %s, bot_id=%s, error=%s",
                    server_code, bot_id, res,
                )
                failures.append(mcp)
            elif res[1]:
                successes.append(mcp)
            else:
                failures.append(mcp)

        success_codes = [
            (m.get("server_code") or m.get("serverCode")) for m in successes
        ]
        failed_codes = [
            (m.get("server_code") or m.get("serverCode")) for m in failures
        ]
        logger.info(
            "[MCPSyncService] 详情同步完成: 成功 %s, 失败 %s, success_codes=%s, failed_codes=%s, "
            "bot_id=%s, entity_id=%s, user_id=%s",
            len(successes), len(failures), success_codes, failed_codes,
            bot_id, entity_id, user_id,
        )

        return successes, failures

    async def _sync_mcp_detail(
        self,
        *,
        plugin: DeviceSyncPlugin,
        user_id: str,
        mcp_data: dict[str, Any],
        api_key: Optional[str] = None,
        custom_headers: Optional[dict[str, str]] = None,
        endpoint_env: Optional[str] = None,
        transport_protocol: Optional[str] = None,
        engine_type: Optional[str] = None,
    ) -> bool:
        """向单个 bot 的设备推送 MCP 配置。

        由 ``sync_mcp_detail``、``sync_mcp_detail_to_all_bots`` 与 ``_sync_mcp_details``
        复用：合并 payload（core 职责）后经 per-bot 的 ``DeviceSyncPlugin`` 投递。

        Args:
            plugin: 由 ``_device_sync_dispatcher_provider().dispatch(ctx)`` 取得的 per-bot 投递插件。
            user_id: 用户 ID。
            mcp_data: MCP 配置数据。
            api_key: 可选的 API key，覆盖默认值。
            custom_headers: 可选的自定义请求头。
            endpoint_env: 可选的 endpoint 环境。
            transport_protocol: 可选的传输协议。
            engine_type: 引擎类型。

        Returns:
            同步是否成功。
        """
        # MCP Center 返回的字段是 camelCase 的 serverCode，
        # 而内部流转时可能已转成 snake_case 的 server_code，需要兼容两种写法。
        server_code = mcp_data.get("serverCode") or mcp_data.get("server_code", "")
        if not server_code:
            logger.warning("[MCPSyncService] MCP 数据缺少 server_code")
            return False

        # 用 MCPConfigService 合并用户自定义配置与默认模板，生成设备端需要的完整 payload。
        _api_key, merged_headers, _endpoint_env, _transport_protocol = self.mcp_config_service.build_mcp_sync_payload(
            user_id=user_id,
            mcp_data=mcp_data,
            api_key=api_key,
            custom_headers=custom_headers,
            endpoint_env=endpoint_env,
            transport_protocol=transport_protocol,
            engine_type=engine_type,
        )

        # 下发到设备；插件内部有 3 次指数退避重试。sync_single_mcp 是同步阻塞 HTTP，
        # 放到线程池执行避免阻塞事件循环。
        return await asyncio.to_thread(
            plugin.sync_single_mcp,
            mcp_data,
            api_key=_api_key,
            custom_headers=merged_headers,
            endpoint_env=_endpoint_env,
            transport_protocol=_transport_protocol,
        )

    def _enrich_from_mcp_center(self, mcps: list[dict[str, Any]]) -> None:
        """用 MCP Center 的元数据补全 MCP 列表。

        BotMCPProvider 返回的 MCP 通常只有 server_code、name 等少量字段；
        推送到设备时需要 endpoint、transportProtocol、tools 等完整信息，
        因此需要再到 MCP Center 查一次详情做补全。
        """
        server_codes = [m.get("server_code") for m in mcps if m.get("server_code")]
        if not server_codes:
            return
        logger.info(
            "[MCPSyncService] 从 MCP Center 拉取 %s 个 MCP 的完整数据",
            len(server_codes),
        )
        # page_size 至少取 20，防止 MCP Center 接口对过小的 page_size 有特殊处理。
        result = self.mcp_center.get_mcp_list(
            page_num=1,
            page_size=max(len(server_codes), 20),
            server_codes=server_codes,
        )
        if result.get("success"):
            mcp_map = {
                m.get("serverCode"): m
                for m in result.get("data", [])
                if m.get("serverCode")
            }
            logger.info(
                "[MCPSyncService] 从 MCP Center 获取到 %s 个 MCP 详情",
                len(mcp_map),
            )
            for mcp in mcps:
                code = mcp.get("server_code")
                if code and code in mcp_map:
                    mcp.update(mcp_map[code])
        else:
            logger.warning(
                "[MCPSyncService] 从 MCP Center 拉取 MCP 详情失败: %s",
                result.get("message"),
            )

    async def _update_passport(
        self,
        bot_id: str,
        user_id: str,
        synced_server_codes: list[str],
        engine_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """通知 passport 系统更新 bot 当前可用的 MCP codes 列表。

        passport 用该列表做前端权限校验等下游消费。bot 元数据同时用于
        解析默认 CLI 授权范围；若查询失败，为避免写入不完整的 CLI 快照，
        本次 passport 更新会中止并返回失败。

        Args:
            bot_id: 目标 bot ID。
            user_id: 用户 ID。
            synced_server_codes: 当前应生效的 MCP server code 列表。
            engine_type: 引擎类型。

        Returns:
            ``{"success": bool, "error": str|None}`` 格式的结果字典。
        """
        bot_name: Optional[str] = None
        bot_desc: Optional[str] = None
        template_type: Optional[str] = None
        try:
            bot = self.bot_repository.get_by_id_and_owner(bot_id, user_id)
            if bot:
                bot_name = bot.get("bot_name")
                bot_desc = bot.get("bot_desc")
                template_type = bot.get("template_type")
                engine_type = (
                    bot.get("active_engine") or bot.get("engine_type") or engine_type
                )
        except Exception as e:
            error = f"获取 bot 信息失败，无法安全解析默认 CLI 范围: {e}"
            logger.error("[MCPSyncService] %s, bot_id=%s", error, bot_id)
            return {"success": False, "error": error}

        # MCP 同步触发 resourceManifest 更新时，要回填当前 CLI，避免覆盖式更新丢失 CLI 授权。
        try:
            current_cli_items = self.passport_update.query_passport_clis(
                bot_id, user_id
            )
        except Exception as e:
            error = f"查询 CLI 范围失败: {e}"
            logger.error("[MCPSyncService] %s", error)
            return {"success": False, "error": error}

        default_cli_items = get_default_cli_items(engine_type, template_type)
        cli_items = _merge_cli_items(current_cli_items, default_cli_items)
        if default_cli_items:
            logger.info(
                "[MCPSyncService] 合并默认 CLI 范围: bot_id=%s, current_clis=%s, "
                "default_clis=%s, merged_clis=%s, engine_type=%s, template_type=%s",
                bot_id,
                current_cli_items,
                default_cli_items,
                cli_items,
                engine_type,
                template_type,
            )

        try:
            # resource_scope 是完整快照：MCP 来自同步结果，CLI 来自当前许可证 + 引擎默认 CLI。
            self.passport_update.update_passport(
                bot_id=bot_id,
                user_id=user_id,
                resource_scope={
                    "mcp_codes": synced_server_codes,
                    "cli_items": cli_items,
                },
                bot_name=bot_name,
                bot_desc=bot_desc,
                engine_type=engine_type,
            )
            logger.info(
                "[MCPSyncService] updatePassport 成功: "
                "bot_id=%s, user_id=%s, mcps=%s, clis=%s, "
                "engine_type=%s, bot_name=%s",
                bot_id,
                user_id,
                synced_server_codes,
                cli_items,
                engine_type,
                bot_name,
            )
            return {"success": True}
        except Exception as e:
            error = f"更新 passport 失败: {e}"
            logger.error("[MCPSyncService] %s", error)
            return {"success": False, "error": error}
