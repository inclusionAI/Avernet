"""Bot Build Service - Bot 构建迁移服务业务逻辑层。

根据 docs/bot-copy-migration-implementation.md 实现的 Bot 构建迁移功能。

执行顺序：
1. Step 1: 查询 AgentClaw 层 Bot 信息
2. Step 2: Bot 实例迁移（使用 Step 1 的信息）
3. Step 3: 调用 BaaS 层创建 Bot（最后一步）

纯业务逻辑层，不包含任何 HTTP 关注点。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from injector import inject

from agentclaw.community.api.channel_service import ChannelServiceProtocol
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.engine_resolver import resolve_engine_for_bot
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.service_bot.services.baas_service import (
    ENGINE_DIR_MOUNT_WHITELIST_BUSINESS_CODE,
    ENGINE_DIR_MOUNT_WHITELIST_PARAM_CODE,
    BaasService,
    BaasServiceError,
)
from agentclaw.community.core.service_bot.services.deploy.engine_ext_stage import (
    DeliveryArtifact,
)
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan, EngineSandboxProvider, EngineSandboxRegistry
from agentclaw.community.core.workspace.path_factory import (
    WorkspacePathFactory,
    get_bot_dir,
    get_bot_nas_dir,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import PassportPlugin

# ``BaasService`` is only used as a type hint here; keep it a
# TYPE_CHECKING-only import so module-load order through
# ``services/__init__.py`` stays acyclic.
if TYPE_CHECKING:
    from agentclaw.community.core.common_config import CommonWhiteListService
    from agentclaw.community.core.devices.services.baas_template_resolver import (
        BaasTemplateResolverProtocol,
    )

logger = get_logger()

# Error codes for which BaaS is telling us an upgrade's target bot is gone, so
# the deploy atom must self-heal with a fresh first release rather than fail.
# ``BOT_NOT_FOUND``: the bot record is gone. ``DEVICE_NOT_FOUND``: the
# underlying device is gone — e.g. a TeClaw bot whose device was physically
# destroyed by an offline STOP. Kept in sync with ``BOT_GONE_ERROR_CODES`` in
# ``publish_flow.operation_runner`` (which classifies the returned result);
# duplicated locally to keep this lower-level service free of a dependency on
# the publish-flow orchestration package.
_BOT_GONE_ERROR_CODES = frozenset({"BOT_NOT_FOUND", "DEVICE_NOT_FOUND"})


class BotBuildServiceError(Exception):
    """Bot build service error."""
    pass


class BotSourceNotFoundError(BotBuildServiceError):
    """Source bot not found error."""
    pass


class BotBuildMigrationError(BotBuildServiceError):
    """Bot migration error."""
    pass


DEVICE_COUNT_CONFIG_BUSINESS_CODE = "service_bot_device_count"
DEVICE_COUNT_DEFAULT_PARAM_CODE = "default"


class BotBuildService:
    """Bot 构建迁移服务 - AgentClaw 层入口。

    执行顺序：
    1. Step 1: 查询 AgentClaw 层 Bot 信息（获取 entity_id, entity_type, device_id, bot_id）
    2. Step 2: Bot 实例迁移（使用 Step 1 的信息 + version）
    3. Step 3: 调用 BaaS 层创建 Bot（最后一步）

    通过依赖注入接收 bot_service 和 device_service。
    """

    @inject
    def __init__(
        self,
        device_service: DeviceService,
        baas_service: BaasService,
        path_factory: WorkspacePathFactory,
        passport_plugin: PassportPlugin,
        device_binding_repo: DeviceBindingRepository,
        sandbox_registry: EngineSandboxRegistry,
        channel_service: ChannelServiceProtocol,
        bot_repository: BotRepository,
        common_whitelist_service: "CommonWhiteListService",
        baas_template_resolver: "BaasTemplateResolverProtocol",
        teclaw_template_uuid: str,
    ):
        """初始化 BotBuildService。

        Args:
            device_service: DeviceService 实例（用于 exec_shell 获取 MCP 配置）
            baas_service: BaasService 实例（用于调用 BaaS 层 API 创建 Bot）
            path_factory: WorkspacePathFactory 实例（用于生成 Bot 路径）
            passport_plugin: PassportPlugin 实例（用于查询 passport token）
            device_binding_repo: DeviceBindingRepository 实例（用于双写设备绑定记录）
            bot_repository: BotRepository 实例（可选，用于双写本地 bot 记录）
        """
        self._device_service = device_service
        self._baas_service = baas_service
        self._path_factory = path_factory
        self._passport_plugin = passport_plugin
        self._bot_repository = bot_repository
        self._device_binding_repository = device_binding_repo
        self._device_binding_repo = device_binding_repo
        self._sandbox_registry = sandbox_registry
        self._channel_service = channel_service
        self._teclaw_template_uuid = teclaw_template_uuid
        self._common_whitelist_service = common_whitelist_service
        self._baas_template_resolver = baas_template_resolver

    def _get_device_binding_repo(self):
        return self._device_binding_repo

    def _resolve_sandbox_provider(self, bot: Dict[str, Any]) -> EngineSandboxProvider:
        engine_type = bot.get("active_engine") or DEFAULT_ENGINE_TYPE
        try:
            return self._sandbox_registry.resolve(engine_type)
        except Exception:
            bot_id = bot.get("bot_id")
            entity_id = bot.get("entity_id", "")
            entity_type = bot.get("entity_type", "staff")
            try:
                resolved_engine = resolve_engine_for_bot(
                    bot_id,
                    entity_id,
                    bot=bot,
                    default_engine=engine_type,
                    entity_type=entity_type,
                )
                return self._sandbox_registry.resolve(resolved_engine)
            except Exception:
                return self._sandbox_registry.resolve(DEFAULT_ENGINE_TYPE)

    def generate_request_id(
        self,
        bot: Dict[str, Any],
        publish_stage: str,
    ) -> str:
        """生成请求 ID。

        使用 entity_id, entity_type, bot_id, env, publish_stage 拼接后生成 hash。

        Args:
            bot: Bot 信息字典，包含: entity_id, entity_type, bot_id, env
            publish_stage: 发布阶段

        Returns:
            32 字符的请求 ID。
        """
        entity_id = bot.get("entity_id", "")
        entity_type = bot.get("entity_type", "staff")
        bot_id = bot.get("bot_id", "")
        env = bot.get("env", "prod")
        raw = f"{entity_id}_{entity_type}_{bot_id}_{env}_{publish_stage}"
        return hashlib.md5(raw.encode()).hexdigest()

    def build(
        self,
        bot: Dict[str, Any],
        version: int = 1,
    ) -> Dict[str, Any]:
        """执行 Bot 构建迁移。

        执行步骤：
        1. Step 1: 计算源目录和目标目录
        2. Step 2: Bot 实例迁移（rsync 复制 + MCP 配置生成）

        Args:
            bot: Bot 信息字典，包含: bot_id, entity_id, entity_type, device_id, bot_name, bot_desc
            version: 迁移版本号（整数），默认 1

        Returns:
            dict: 构建结果信息
                - success: 构建是否成功
                - bot_id: 源 Bot ID
                - entity_id: 实体 ID
                - entity_type: 实体类型
                - version: 版本号
                - migration_path: 迁移目录路径

        Raises:
            BotBuildMigrationError: 迁移失败
            BotBuildServiceError: 其他服务错误
        """
        # 版本号转换为字符串格式（用于目录路径）
        version_str = str(version)
        bot_id = bot.get("bot_id")
        entity_id = bot.get("entity_id", "")
        entity_type = bot.get("entity_type", "staff")
        device_id = bot.get("device_id")
        provider = self._resolve_sandbox_provider(bot)
        build_plan = provider.get_build_plan()
        engine_type = build_plan.engine_type
        logger.info(
            f"[BotBuildService.build] Starting build: "
            f"bot_id={bot_id}, version={version_str}"
        )

        try:
            # ============================================================
            # Step 1: 计算源目录和目标目录（统一走 NAS 存储）
            # ============================================================
            nas_storage_id = get_bot_nas_dir(
                entity_id=entity_id,
                bot_id=bot_id,
                engine_type=engine_type,
                entity_type=entity_type,
            )
            source_dir = nas_storage_id / build_plan.source_root_name
            logger.info(
                f"[BotBuildService.build] using NAS source_dir={source_dir}"
            )

            target_dir = get_bot_dir(
                entity_id=entity_id,
                bot_id=bot_id,
                entity_type=entity_type,
            ) / version_str / build_plan.migration_subpath

            logger.info(
                f"[BotBuildService.build] Step 1: "
                f"source_dir={source_dir}, target_dir={target_dir}"
            )

            # ============================================================
            # Step 2: Bot 实例迁移
            # ============================================================
            # 2.1 执行 rsync 迁移
            migration_success = self._migrate_bot_instance(
                device_id=device_id,
                source_dir=source_dir,
                target_dir=target_dir,
                version_str=version_str,
                is_nas=True,
                nas_storage_id=nas_storage_id,
                build_plan=build_plan,
                provider=provider,
            )

            # 2.2 生成 MCP 配置（使用 bot 的 device_id）
            mcp_success = True
            if device_id and migration_success:
                mcp_success = self._generate_mcp_config(
                    device_id=device_id,
                    target_dir=target_dir,
                    build_plan=build_plan,
                )

            # 2.3 生成多阶段 OpenClaw 配置
            openclaw_configs_success = True
            if migration_success and engine_type == "openclaw":
                openclaw_configs_success = self._generate_openclaw_stage_configs(
                    bot_id=bot_id,
                    owner_id=entity_id,
                    target_dir=target_dir,
                )

            logger.info(
                f"[BotBuildService.build] Step 2 completed: "
                f"migration_success={migration_success}, "
                f"mcp_success={mcp_success}, "
                f"openclaw_configs_success={openclaw_configs_success}"
            )

            # 构建结果
            success = migration_success and mcp_success and openclaw_configs_success
            migration_path_base = self._get_migration_path_base(
                owner_id=entity_id,
                bot_id=bot_id,
            )
            result = {
                "success": success,
                "bot_id": bot_id,
                "entity_id": entity_id,
                "entity_type": entity_type,
                "version": version_str,
                "migration_path": f"{migration_path_base}/{version_str}/{build_plan.migration_subpath}",
                "build_target_path": str(target_dir),
                "mcp_success": mcp_success,
                "openclaw_configs_success": openclaw_configs_success,
            }

            logger.info(
                f"[BotBuildService.build] Build completed: "
                f"success={success}, migration_path={str(target_dir)}"
            )

            return result

        except BotBuildMigrationError:
            raise
        except Exception as e:
            logger.error(f"[BotBuildService.build] Unexpected error: {e}")
            raise BotBuildServiceError(f"Bot build failed: {e}")

    def _get_migration_path_base(self, *, owner_id: str, bot_id: str) -> str:
        """根据白名单选择容器内迁移路径根目录。

        命中引擎目录挂载白名单时，BaaS storage 将 home 目录挂到 NAS，
        bot-data 的实际访问路径切换为 /opt/nfs/bot-data；否则沿用旧路径
        /home/admin/nfs/bot-data。配置异常时 fail closed，回退旧路径。
        """
        if self._should_mount_home_dir_storage(owner_id=owner_id, bot_id=bot_id):
            return "/opt/nfs/bot-data"
        return "/home/admin/nfs/bot-data"

    def _should_mount_home_dir_storage(self, *, owner_id: str, bot_id: str) -> bool:
        """查询引擎目录挂载白名单，读取失败时 fail closed 回退旧路径。"""
        whitelist_service = self._common_whitelist_service

        try:
            from agentclaw.community.utils.env_utils import get_current_env

            return whitelist_service.is_bot_feature_enabled(
                business_code=ENGINE_DIR_MOUNT_WHITELIST_BUSINESS_CODE,
                param_code=ENGINE_DIR_MOUNT_WHITELIST_PARAM_CODE,
                owner_id=owner_id,
                bot_id=bot_id,
                env=get_current_env(),
                default=False,
            )
        except Exception as exc:
            logger.warning(
                "[BotBuildService._should_mount_home_dir_storage] "
                "Failed to check whitelist, fallback to legacy migration path: "
                "owner_id=%s, bot_id=%s, error=%s",
                owner_id,
                bot_id,
                exc,
            )
            return False

    def _resolve_baas_template_uuid(
        self,
        *,
        bot: Dict[str, Any],
        user_id: str,
    ) -> str | None:
        """服务 Bot 发布到 BaaS 前，按业务上下文解析 template_uuid。

        没有注入 resolver 时保持历史行为，由 BaasService 使用默认
        baas.template_uuid；注入后与设备申请链路共用同一份 system_config。
        """
        template_resolver = self._baas_template_resolver

        from agentclaw.community.utils.env_utils import get_current_env

        bot_id = str(bot.get("bot_id") or "")
        env = get_current_env()
        template_config = bot.get("template_config")
        if not isinstance(template_config, dict):
            template_config = None

        template = template_resolver.resolve_template(
            bot_id=bot_id,
            user_id=user_id,
            env=env,
            bot_type=str(bot.get("bot_type") or "service"),
            engine_type=bot.get("active_engine"),
            template_type=bot.get("template_type"),
            template_config=template_config,
        )
        logger.info(
            "[BotBuildService.template] resolved: bot_id=%s user_id=%s "
            "engine_type=%s template_type=%s template_uid=%s template_uuid=%s",
            bot_id,
            user_id,
            bot.get("active_engine"),
            bot.get("template_type"),
            template.template_uid,
            template.template_uuid,
        )
        return template.template_uuid

    async def build_async(
        self,
        bot: Dict[str, Any],
        version: int = 1,
    ) -> Dict[str, Any]:
        """异步执行 Bot 构建迁移。

        使用 asyncio.to_thread() 将同步 build() 方法包装为异步执行，
        避免阻塞事件循环。

        Args:
            bot: Bot 信息字典，包含: bot_id, entity_id, entity_type, device_id, bot_name, bot_desc
            version: 迁移版本号（整数），默认 1

        Returns:
            dict: 构建结果信息（与 build() 返回值相同）

        Raises:
            BotBuildMigrationError: 迁移失败
            BotBuildServiceError: 其他服务错误
        """
        logger.info(
            f"[BotBuildService.build_async] Starting async build: "
            f"bot_id={bot.get('bot_id')}, version={version}"
        )
        # 使用 asyncio.to_thread 在线程池中执行同步方法
        return await asyncio.to_thread(self.build, bot, version)

    def release(
        self,
        bot: Dict[str, Any],
        user_id: str,
        migration_path: str,
        device_count: int = 1,
        publish_stage: PublishStage = PublishStage.VERIFY,
        version: str = "1",
        delivery: DeliveryArtifact = DeliveryArtifact(None),
        ext_info: Optional[Dict[str, Any]] = None,
        extra_envs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """发布 Bot 到 BaaS 层。

        独立的发布方法，用于将已迁移的 Bot 发布到 BaaS 层。
        可在 build 方法中调用，也可独立使用。

        Args:
            bot: Bot 信息字典，包含: bot_id, entity_id, entity_type, bot_name, bot_desc
            user_id: 用户 ID（创建者）
            migration_path: Bot 实例迁移后的目录路径
            device_count: 设备数量，默认 1
            publish_stage: 发布推进阶段，默认验证阶段
            version: 迁移版本号（字符串），默认 "1"
            delivery: 组合后的交付 Artifact（DeliveryArtifact）。只能由
                PublishExtState 的 compose seam 产生，流程层无法直接传原始
                config_artifact；ARCA 挂载路径为 config_artifact=None。
            ext_info: Dict[str, Any] = None,

        Returns:
            dict: BaaS 层返回的 Bot 信息（包含 bot_uuid）

        Raises:
            BotBuildServiceError: 发布失败
        """

        # 生成 request_id
        request_id = self.generate_request_id(
            bot=bot,
            publish_stage=publish_stage.value,
        )

        bot_id = bot.get("bot_id")

        logger.info(
            f"[BotBuildService.release] Starting release: "
            f"bot_id={bot_id}, user_id={user_id}, publish_stage={publish_stage.value}, request_id={request_id}"
        )

        # 查询 passport token（非阻塞，失败不影响发布）
        agent_pass_token = ""
        owner_id = bot["entity_id"]
        try:
            agent_pass_token = self._passport_plugin.query_token(bot_id, owner_id) or ""
            logger.info(
                f"[BotBuildService.release] queryToken success: bot_id={bot_id}, "
                f"owner_id={owner_id}, token_prefix={agent_pass_token[:20] if agent_pass_token else ''}"
            )
        except Exception as e:
            logger.warning(
                f"[BotBuildService.release] queryToken failed: bot_id={bot_id}, "
                f"owner_id={owner_id}, error={e}"
            )

        try:
            is_teclaw_release = (
                self._baas_service.resolve_container_provider(bot)
                == TECLAW_DEVICE_PROVIDER
            )
            if is_teclaw_release:
                # teclaw: non-mount delivery — hand the frozen artifact to BaaS,
                # which forwards it to the external container. No migration_path.
                # The artifact IS the delivery payload, so fail loudly if it is
                # missing rather than provisioning a container with empty config.
                if not delivery.config_artifact:
                    raise BotBuildServiceError(
                        "teclaw release requires a composed config_artifact; "
                        f"got {delivery.config_artifact!r}"
                    )
                new_bot = self._baas_service.create_teclaw_bot(
                    bot,
                    owner_id=user_id,
                    request_id=request_id,
                    config_artifact=delivery.config_artifact,
                    template_uuid=self._teclaw_template_uuid,
                    device_count=1,
                )
            else:
                create_kwargs: dict[str, Any] = {
                    "bot": bot,
                    "owner_id": user_id,
                    "request_id": request_id,
                    "device_count": device_count,
                    "migration_path": migration_path,
                    "agent_pass_token": agent_pass_token,
                    "stage": publish_stage.value,
                    "version": version,
                    "ext_info": ext_info,
                    "extra_envs": extra_envs,
                }
                template_uuid = self._resolve_baas_template_uuid(
                    bot=bot,
                    user_id=user_id,
                )
                if template_uuid is not None:
                    create_kwargs["template_uuid"] = template_uuid
                new_bot = self._baas_service.create_bot(**create_kwargs)

            bot_uuid = new_bot.get("bot_uuid")
            publish_id = new_bot.get("publish_id")

            logger.info(
                f"[BotBuildService.release] Release completed: "
                f"target_bot_uuid={bot_uuid}, publish_id={publish_id}"
            )

            return new_bot

        except Exception as e:
            logger.error(f"[BotBuildService.release] Release failed: {e}")
            raise BotBuildServiceError(f"Bot release failed: {e}")

    async def release_async(
        self,
        bot: Dict[str, Any],
        user_id: str,
        migration_path: str,
        device_count: int = 1,
        publish_stage: PublishStage = PublishStage.VERIFY,
        version: str = "1",
        delivery: DeliveryArtifact = DeliveryArtifact(None),
        ext_info: Optional[Dict[str, Any]] = None,
        extra_envs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """异步发布 Bot 到 BaaS 层。

        使用 asyncio.to_thread() 将同步 release() 方法包装为异步执行，
        避免阻塞事件循环。

        Args:
            bot: Bot 信息字典，包含: bot_id, entity_id, entity_type, bot_name, bot_desc
            user_id: 用户 ID（创建者）
            migration_path: Bot 实例迁移后的目录路径
            device_count: 设备数量，默认 1
            publish_stage: 发布推进阶段，默认验证阶段
            version: str = "1",发布单版本
            delivery: 组合后的交付 Artifact（DeliveryArtifact）；见 release()。
            ext_info: Optional[Dict[str, Any]] = None,

        Returns:
            dict: BaaS 层返回的 Bot 信息（包含 bot_uuid）

        Raises:
            BotBuildServiceError: 发布失败
        """
        logger.info(
            f"[BotBuildService.release_async] Starting async release: "
            f"bot_id={bot.get('bot_id')}, user_id={user_id}, publish_stage={publish_stage.value}"
        )
        # 使用 asyncio.to_thread 在线程池中执行同步方法
        return await asyncio.to_thread(
            self.release, bot, user_id, migration_path, device_count, publish_stage, version,
            delivery, ext_info,
            extra_envs,
        )

    def _sync_skill_links(
        self,
        device_id: str,
        version: str,
        build_plan: EngineBuildPlan,
        provider: EngineSandboxProvider,
    ) -> None:
        """同步 skill 软链到目标目录。

        Args:
            device_id: 设备 ID，用于执行远程命令
            version: 版本号，用于拼接目标路径
            build_plan: 引擎构建计划
            provider: 当前引擎 provider

        Raises:
            BotBuildMigrationError: 同步失败
        """
        try:
            source_base_path = provider.get_base_path().rstrip('/')
            source_skill_path = f"{source_base_path}/{build_plan.skill_source_relpath.strip('/')}"
            target_skill_path = (
                f"/home/admin/nfs/bot-data/{version}/"
                f"{build_plan.migration_subpath}/{build_plan.skill_target_relpath.strip('/')}"
            )
            skill_cmd = (
                "rsync -av --exclude='skills-repo' --exclude='.skills-repo*' "
                f"{source_skill_path}/ {target_skill_path}/"
            )

            logger.info(
                f"[BotBuildService._sync_skill_links] "
                f"Running rsync skill command:{skill_cmd}"
            )

            self._device_service.exec_shell_new(
                device_id=device_id,
                shell_cmd=skill_cmd,
            )

            logger.info(
                "[BotBuildService._sync_skill_links] "
                "Skill links synced successfully"
            )
        except Exception as e:
            logger.warning(
                f"[BotBuildService._sync_skill_links] "
                f"Failed to synced links: {device_id}, {e}"
            )

    def _run_local_command(
        self,
        cmd: list[str],
        command_name: str,
        error_message: str,
    ) -> subprocess.CompletedProcess:
        """执行本地命令并统一处理日志和错误。

        Args:
            cmd: 命令参数列表
            command_name: 命令名称，用于日志
            error_message: 命令执行失败时的业务错误信息前缀

        Returns:
            subprocess.CompletedProcess: 命令执行结果

        Raises:
            BotBuildMigrationError: 命令不存在或执行失败
        """
        logger.info(
            f"[BotBuildService._run_local_command] "
            f"Running {command_name} command: {' '.join(cmd)}"
        )

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            logger.error(
                f"[BotBuildService._run_local_command] "
                f"{command_name} command not found"
            )
            raise BotBuildMigrationError(
                f"{command_name} command not found"
            )

        if result.returncode != 0:
            logger.error(
                f"[BotBuildService._run_local_command] "
                f"{command_name} failed: {result.stderr}"
            )
            raise BotBuildMigrationError(
                f"{error_message}: {result.stderr}"
            )

        return result

    def _migrate_bot_instance(
        self,
        device_id: str,
        source_dir: Path,
        target_dir: Path,
        version_str: str,
        is_nas: bool = False,
        nas_storage_id: Path | str | None = None,
        build_plan: EngineBuildPlan | None = None,
        provider: EngineSandboxProvider | None = None,
    ) -> bool:
        """Step 2.3: 执行 Bot 实例 rsync 迁移。

        Args:
            source_dir: 源目录路径（源 Bot 的工作区）
            target_dir: 目标目录路径（使用源 Bot 信息 + version）

        Returns:
            是否迁移成功

        Raises:
            BotBuildMigrationError: 迁移失败
        """
        logger.info(
            f"[BotBuildService._migrate_bot_instance] "
            f"Starting migration: {source_dir} -> {target_dir}"
        )

        if is_nas:
            self._run_local_command(
                cmd=["sudo", "chmod", "755", str(nas_storage_id)],
                command_name="sudo chmod",
                error_message="chmod source directory failed",
            )

        if not source_dir.exists():
            logger.warning(
                f"[BotBuildService._migrate_bot_instance] "
                f"Source directory does not exist: {source_dir}"
            )
            return False

        logger.info(
            f"[BotBuildService._migrate_bot_instance] "
            f"Starting execute command: sudo chmod when is {is_nas}"
        )

        if not is_nas:
            try:
                # 重启 sync 服务确保构建前强制同步
                self._device_service.exec_shell_new(
                    device_id=device_id,
                    shell_cmd="supervisorctl restart sync",
                )
                time.sleep(10)
            except Exception as e:
                logger.error(
                    f"[BotBuildService._migrate_bot_instance] exec shell cmd supervisorctl restart sync"
                    f"Unexpected error: {e}"
                )

        try:
            # 确保目标目录存在
            target_dir.mkdir(parents=True, exist_ok=True)

            # 构建 rsync 排除参数
            excludes = []
            for pattern in (build_plan.rsync_excludes if build_plan else []):
                excludes.append(f"--exclude={pattern}")

            # 构建 rsync 命令
            cmd = [
                "sudo",
                "rsync",
                "-av",           # 归档模式 + 详细输出
                "--delete",       # 删除目标目录中不存在于源目录的文件
                *excludes,
                f"{source_dir}/",
                f"{target_dir}/",
            ]

            self._run_local_command(
                cmd=cmd,
                command_name="rsync",
                error_message="rsync migration failed",
            )

            # 额外同步目录：例如 claude_code 引擎需要把 .claude 同步到 target/claude
            extra_src_rel = build_plan.extra_sync_source_relpath if build_plan else ""
            extra_tgt_rel = build_plan.extra_sync_target_relpath if build_plan else ""
            if extra_src_rel and extra_tgt_rel:
                extra_source = source_dir.parent / extra_src_rel
                extra_target = target_dir / extra_tgt_rel

                if not extra_source.exists():
                    raise BotBuildMigrationError(
                        f"Extra sync source does not exist: {extra_source}"
                    )

                extra_target.mkdir(parents=True, exist_ok=True)

                extra_cmd = [
                    "sudo",
                    "rsync",
                    "-av",
                    "--delete",
                    *excludes,
                    f"{extra_source}/",
                    f"{extra_target}/",
                ]
                self._run_local_command(
                    cmd=extra_cmd,
                    command_name="rsync (extra)",
                    error_message="rsync extra sync failed",
                )

            # 同步 skill 软链到目标目录
            if build_plan and provider:
                self._sync_skill_links(device_id, version_str, build_plan, provider)

            logger.info(
                f"[BotBuildService._migrate_bot_instance] "
                f"g: {source_dir} -> {target_dir}"
            )

            return True

        except BotBuildMigrationError:
            raise
        except Exception as e:
            logger.error(
                f"[BotBuildService._migrate_bot_instance] "
                f"Unexpected error: {e}"
            )
            raise BotBuildMigrationError(f"Migration failed: {e}")

    def _generate_openclaw_stage_configs(
        self,
        *,
        bot_id: str,
        owner_id: str,
        target_dir: Path,
    ) -> bool:
        """生成发布阶段 OpenClaw 配置文件。

        调用 ChannelService.generate_openclaw_configs 获取不同发布阶段的
        OpenClaw JSON 内容，并写入构建迁移目标目录。生成文件命名为
        openclaw_{stage}.json，例如 openclaw_verify.json / openclaw_online.json。

        Args:
            bot_id: Bot ID
            owner_id: Bot 所属用户身份 ID
            target_dir: 构建迁移目标目录

        Returns:
            是否生成成功。失败时记录日志并返回 False，与 MCP 配置生成行为保持一致。
        """
        logger.info(
            f"[BotBuildService._generate_openclaw_stage_configs] "
            f"Generating OpenClaw stage configs: "
            f"bot_id={bot_id}, owner_id={owner_id}, target_dir={target_dir}"
        )

        try:
            configs = asyncio.run(
                self._channel_service.generate_openclaw_configs(
                    bot_id=bot_id,
                    owner_id=owner_id,
                )
            )
            config_map = vars(configs)

            target_dir.mkdir(parents=True, exist_ok=True)

            for stage, content in config_map.items():
                if not content:
                    logger.info(
                        f"[BotBuildService._generate_openclaw_stage_configs] "
                        f"Skip empty OpenClaw config: stage={stage}"
                    )
                    continue

                config_path = target_dir / f"openclaw_{stage}.json"
                config_path.write_text(content, encoding="utf-8")
                logger.info(
                    f"[BotBuildService._generate_openclaw_stage_configs] "
                    f"OpenClaw config written: stage={stage}, path={config_path}"
                )

            return True

        except Exception as e:
            logger.warning(
                f"[BotBuildService._generate_openclaw_stage_configs] "
                f"Failed to generate OpenClaw stage configs: "
                f"bot_id={bot_id}, owner_id={owner_id}, error={e}"
            )
            return False

    def _generate_mcp_config(
        self,
        device_id: str,
        target_dir: Path,
        build_plan: EngineBuildPlan | None = None,
    ) -> bool:
        """Step 2.4: 从源设备生成 MCP 配置。

        Args:
            device_id: 源设备 ID（来自 Step 1 查询的 Bot 信息）
            target_dir: 目标目录路径

        Returns:
            是否生成成功
        """
        logger.info(
            f"[BotBuildService._generate_mcp_config] "
            f"Generating MCP config: device_id={device_id}, target_dir={target_dir}"
        )

        try:
            # 从容器获取 mcporter.json 内容
            result = self._device_service.exec_shell_new(
                device_id=device_id,
                shell_cmd="cat /home/admin/.mcporter/mcporter.json",
            )

            logger.info(
                f"[BotBuildService._generate_mcp_config] result={result}"

            )

            # 检查返回结果
            if not result.stdout or not result.stdout.strip():
                logger.warning(
                    f"[BotBuildService._generate_mcp_config] "
                    f"Empty stdout from device {device_id}, stderr={result.stderr}"
                )
                return False

            # 解析 JSON
            try:
                mcporter_content = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                logger.warning(
                    f"[BotBuildService._generate_mcp_config] "
                    f"Invalid JSON from device {device_id}: {e}"
                )
                return False

            if not mcporter_content:
                logger.warning(
                    f"[BotBuildService._generate_mcp_config] "
                    f"Empty mcporter.json content from device {device_id}"
                )
                return False

            # 处理返回值：dict 或 list 都用 json.dumps
            if isinstance(mcporter_content, (dict, list)):
                content_str = json.dumps(mcporter_content, ensure_ascii=False, indent=2)
            else:
                content_str = str(mcporter_content)

            # 写入目标配置路径
            if build_plan:
                config_path = target_dir / Path(build_plan.mcp_config_relpath)
            else:
                config_path = target_dir / "workspace" / "config" / "mcporter.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(content_str, encoding="utf-8")

            logger.info(
                f"[BotBuildService._generate_mcp_config] "
                f"MCP config written to: {config_path}"
            )

            return True

        except Exception as e:
            logger.warning(
                f"[BotBuildService._generate_mcp_config] "
                f"Failed to generate MCP config: {e}"
            )
            # MCP 配置生成失败不阻塞流程，只记录警告
            return False

    def _extract_baas_error_info(self, error: Exception) -> Dict[str, Any]:
        """从 BaaS 异常中提取结构化错误信息。"""

        def _payload_from_candidate(candidate: Any) -> Dict[str, Any]:
            if isinstance(candidate, dict):
                payload = candidate
            elif isinstance(candidate, str):
                try:
                    payload = json.loads(candidate)
                except Exception:
                    return {}
            elif isinstance(candidate, BaseException):
                nested = self._extract_baas_error_info(candidate)
                if nested:
                    return nested
                return {}
            else:
                return {}

            detail = payload.get("detail")
            if isinstance(detail, dict):
                return detail
            return payload if isinstance(payload, dict) else {}

        candidates: list[Any] = []

        response = getattr(error, "response", None)
        if response is not None:
            try:
                candidates.append(response.json())
            except Exception:
                text_body = getattr(response, "text", None)
                if text_body:
                    candidates.append(text_body)

        if getattr(error, "args", None):
            candidates.extend(error.args)

        for candidate in candidates:
            payload = _payload_from_candidate(candidate)
            if payload:
                return payload

        return {}

    def upgrade(
            self,
            bot_uuid: str,
            bot: Dict[str, Any],
            user_id: str,
            migration_path: str,
            device_count: int = 1,
            publish_stage: PublishStage = PublishStage.ONLINE,
            version: str = "1",
            delivery: DeliveryArtifact = DeliveryArtifact(None),
            extra_envs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """升级 Bot 到 BaaS 层（复用现有 Bot）。

        与 release() 的区别：
        - release() 调用 create_bot() 创建新 Bot
        - upgrade() 调用 upgrade_bot() 更新现有 Bot

        Args:
            bot_uuid: 现有 Bot 的 UUID（从 last_pub 的 binding 获取）
            bot: Bot 信息字典，包含: bot_id, entity_id, entity_type, bot_name, bot_desc
            user_id: 用户 ID（操作者）
            migration_path: Bot 实例迁移后的目录路径
            device_count: 设备数量，默认 1
            publish_stage: 发布推进阶段，默认 ONLINE
            version: str = "1", 发布版本
            delivery: 组合后的交付 Artifact（DeliveryArtifact）；见 release()。

        Returns:
            dict: BaaS 层返回的 Bot 信息（包含 bot_uuid, publish_id）
        """
        request_id = self.generate_request_id(
            bot=bot,
            publish_stage=f"upgrade_{publish_stage.value}",
        )

        bot_id = bot.get("bot_id")

        logger.info(
            f"[BotBuildService.upgrade] Starting upgrade: "
            f"bot_id={bot_id}, bot_uuid={bot_uuid}, user_id={user_id}"
        )

        try:
            if (
                self._baas_service.resolve_container_provider(bot)
                == TECLAW_DEVICE_PROVIDER
            ):
                # teclaw re-publish: re-deliver the frozen artifact to the
                # existing container (non-mount). No migration_path. The artifact
                # IS the delivery payload, so fail loudly if it is missing rather
                # than re-delivering empty config to the existing container.
                if not delivery.config_artifact:
                    raise BotBuildServiceError(
                        "teclaw upgrade requires a composed config_artifact; "
                        f"got {delivery.config_artifact!r}"
                    )
                upgrade_result = self._baas_service.update_teclaw_bot(
                    bot_uuid,
                    bot,
                    owner_id=user_id,
                    request_id=request_id,
                    config_artifact=delivery.config_artifact,
                    template_uuid=self._teclaw_template_uuid,
                    device_count=1,
                )
            else:
                upgrade_kwargs: dict[str, Any] = {
                    "bot_uuid": bot_uuid,
                    "bot": bot,
                    "owner_id": user_id,
                    "request_id": request_id,
                    "migration_path": migration_path,
                    "device_count": device_count,
                    "stage": publish_stage.value,
                    "version": version,
                    "extra_envs": extra_envs,
                }
                template_uuid = self._resolve_baas_template_uuid(
                    bot=bot,
                    user_id=user_id,
                )
                if template_uuid is not None:
                    upgrade_kwargs["template_uuid"] = template_uuid
                upgrade_result = self._baas_service.upgrade_bot(**upgrade_kwargs)

            logger.info(
                f"[BotBuildService.upgrade] Upgrade completed: "
                f"bot_uuid={bot_uuid}, publish_id={upgrade_result.get('publish_id')}"
            )

            return upgrade_result

        except Exception as e:
            error_info = self._extract_baas_error_info(e)
            error_code = error_info.get("error_code")
            # BaaS signals a gone upgrade target as either BOT_NOT_FOUND (bot
            # record gone) or DEVICE_NOT_FOUND (underlying device gone — e.g. a
            # TeClaw bot whose device was destroyed by an offline STOP). Surface
            # both as a structured failure so the deploy atom classifies it and
            # the caller self-heals with a fresh first release, instead of
            # letting DEVICE_NOT_FOUND raise and fail the publish outright.
            if error_code in _BOT_GONE_ERROR_CODES:
                logger.warning(
                    f"[BotBuildService.upgrade] Upgrade hit {error_code}: "
                    f"bot_uuid={bot_uuid}, error_info={error_info}"
                )
                return {
                    "success": False,
                    "error_code": error_code,
                    "message": error_info.get("message") or str(e),
                    "bot_uuid": bot_uuid,
                }

            logger.error(f"[BotBuildService.upgrade] Upgrade failed: {e}")
            raise BotBuildServiceError(f"Bot upgrade failed: {e}")

    def retire_superseded_bot(
        self, bot_uuid: str, operator: str = "system"
    ) -> int | None:
        """Idempotent teardown of a bot that a deploy is superseding.

        Call this ONLY for a bot the caller has already decided is gone or not
        reusable (e.g. a teclaw ``FAILED``/``STOPPED`` online bot whose container
        an ``upgrade`` cannot rebuild, or a lingering record surfaced by a
        ``DEVICE_NOT_FOUND`` fallback). It exists so that when a new online bot is
        created instead of reusing the old one, the old one is not left behind as
        an orphan (dirty data + wasted provider compute).

        Returns the BaaS DESTROY workflow id when one was issued, or ``None`` when
        the bot was already gone (nothing to destroy) — the caller stashes it on
        the released binding.

        Failures **propagate** (AGENTS.md: never swallow a failed lifecycle write
        and report success): if the destroy cannot be confirmed, the caller must
        NOT proceed to create the replacement, or the superseded bot stays live
        and the at-most-one-live-bot guarantee is broken. The durable deploy then
        retries — and on retry the decision is re-evaluated. The one exception is
        an **already-gone** bot: if the candidate was deleted between the
        decision's status read and this call, BaaS rejects the DESTROY (404, which
        ``get_bot`` normalizes to ``RELEASED``; or ``DESTROYING`` when a prior
        destroy is already in flight). That already satisfies the retirement goal,
        so we re-check and treat an explicitly-gone bot as success — while any
        other destroy failure (timeout, conflict, 5xx, still-live bot) propagates.
        The ``request_id`` is derived deterministically from ``bot_uuid`` so a
        redelivery reuses the same idempotent destroy rather than opening a second
        one.
        """
        request_id = hashlib.md5(f"retire_{bot_uuid}".encode()).hexdigest()
        try:
            result = self._baas_service.destroy_bot(
                bot_uuid=bot_uuid,
                operator=operator,
                request_id=request_id,
            )
        except BaasServiceError:
            # The destroy may have been rejected because the bot is ALREADY gone
            # (deleted after the decision's status read, or a destroy already in
            # flight). Re-check: only an explicit gone status satisfies the goal;
            # anything else (still live, or an ambiguous empty response) means the
            # destroy genuinely failed and must propagate.
            baas_bot = self._baas_service.get_bot(bot_uuid=bot_uuid)
            status = (baas_bot or {}).get("status")
            if status not in ("RELEASED", "DESTROYING"):
                raise
            logger.info(
                f"[BotBuildService.retire_superseded_bot] destroy rejected but bot "
                f"already gone (status={status}); retirement satisfied: "
                f"bot_uuid={bot_uuid}"
            )
            return None
        publish_id = result.get("publish_id") if isinstance(result, dict) else None
        if publish_id is None:
            # destroy_bot returned a successful-but-empty envelope (no workflow id):
            # the DESTROY was NOT confirmed initiated. Do NOT report success (that
            # would let the caller create a replacement while the old bot may still
            # be live) — propagate so the durable deploy retries. ``None`` is
            # reserved for the explicit already-gone recheck path above.
            raise BotBuildServiceError(
                f"destroy_bot returned no publish_id for bot_uuid={bot_uuid}; "
                f"destroy not confirmed"
            )
        logger.info(
            f"[BotBuildService.retire_superseded_bot] retired superseded bot: "
            f"bot_uuid={bot_uuid}, operator={operator}, destroy_publish_id={publish_id}"
        )
        return publish_id

    def refresh_teclaw_mcp_outbound_rule(
        self,
        *,
        bot_uuid: str,
        bot: Dict[str, Any],
    ) -> bool:
        """刷新 Teclaw 运行实例用于 MCP 鉴权的 outbound rule。"""
        if (
            self._baas_service.resolve_container_provider(bot)
            != TECLAW_DEVICE_PROVIDER
        ):
            return False

        bot_id = bot.get("bot_id")
        owner_id = bot.get("entity_id")
        if not bot_id or not owner_id:
            logger.warning(
                "[BotBuildService.refresh_teclaw_mcp_outbound_rule] missing context: "
                "bot_id=%s, owner_id=%s, bot_uuid=%s",
                bot_id,
                owner_id,
                bot_uuid,
            )
            return False

        try:
            agent_pass_token = self._passport_plugin.query_token(bot_id, owner_id) or ""
        except Exception as e:
            logger.warning(
                "[BotBuildService.refresh_teclaw_mcp_outbound_rule] queryToken failed: "
                "bot_id=%s, owner_id=%s, error=%s",
                bot_id,
                owner_id,
                e,
            )
            return False

        if not agent_pass_token:
            logger.warning(
                "[BotBuildService.refresh_teclaw_mcp_outbound_rule] queryToken empty: "
                "bot_id=%s, owner_id=%s",
                bot_id,
                owner_id,
            )
            return False

        try:
            updated = self._baas_service.update_teclaw_outbound_rule_by_bot_uuid(
                bot_uuid,
                agent_pass_token=agent_pass_token,
            )
            if updated is not None and not updated:
                # Devices aren't ready (no device / no provider_device_id yet) —
                # nothing was written, so don't report a delivery that didn't
                # happen. The caller's next publish-progress SUCCESS re-runs this.
                logger.warning(
                    "[BotBuildService.refresh_teclaw_mcp_outbound_rule] no ready "
                    "device to deliver to: bot_id=%s, bot_uuid=%s",
                    bot_id,
                    bot_uuid,
                )
                return False
            return True
        except Exception as update_err:
            logger.warning(
                "[BotBuildService.refresh_teclaw_mcp_outbound_rule] update failed: "
                "bot_id=%s, bot_uuid=%s, error=%s",
                bot_id,
                bot_uuid,
                update_err,
            )
            return False

    async def upgrade_async(
        self,
        bot_uuid: str,
        bot: Dict[str, Any],
        user_id: str,
        migration_path: str,
        device_count: int = 1,
        publish_stage: PublishStage = PublishStage.ONLINE,
        version: str = "1",
        delivery: DeliveryArtifact = DeliveryArtifact(None),
        extra_envs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """异步升级 Bot 到 BaaS 层。

        使用 asyncio.to_thread() 将同步 upgrade() 方法包装为异步执行。
        """
        logger.info(
            f"[BotBuildService.upgrade_async] Starting async upgrade: "
            f"bot_id={bot.get('bot_id')}, bot_uuid={bot_uuid}"
        )

        # 使用 asyncio.to_thread 在线程池中执行同步方法
        return await asyncio.to_thread(
            self.upgrade, bot_uuid, bot, user_id, migration_path, device_count, publish_stage, version,
            delivery,
            extra_envs,
        )
