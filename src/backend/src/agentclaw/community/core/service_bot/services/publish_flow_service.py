"""Bot 发布流程处理服务。

根据发布单状态推进发布流程的不同阶段。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from injector import inject

from agentclaw.community.core.service_bot.repository.models import PublishStatus, BotPublishRecord
from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult
from agentclaw.community.core.service_bot.services.bot_build_service import BotBuildService
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    BotPublishService,
    PublishNotFoundError,
    PublishStatusInvalidError,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifactProducerRouter,
)
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.core.common_config.service import CommonConfigService
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.services.publish_flow.ext_state import (
    PublishExtState,
)
from agentclaw.community.core.service_bot.services.publish_flow.provider_behavior import (
    DefaultProviderBehavior,
    ProviderBehaviorRouter,
    TeclawProviderBehavior,
)
from agentclaw.community.core.service_bot.services.publish_flow.build_stage import (
    BuildStageRunner,
)
from agentclaw.community.core.service_bot.services.publish_flow.progress_sync_mixin import (
    ProgressSyncMixin,
)
from agentclaw.community.core.service_bot.services.publish_flow.restart_mixin import (
    RestartMixin,
)
from agentclaw.community.core.service_bot.services.publish_flow.scale_mixin import (
    ScaleMixin,
)
from agentclaw.community.core.service_bot.services.publish_flow.stage_status_mixin import (
    StageStatusMixin,
)
from agentclaw.community.core.service_bot.services.publish_flow.rollback_ops_mixin import (
    RollbackOpsMixin,
)
from agentclaw.community.core.service_bot.services.publish_flow.baas_publish_ops_mixin import (
    BaasPublishOpsMixin,
)
from agentclaw.community.core.service_bot.services.publish_flow.eval_publish_mixin import (
    EvalPublishMixin,
)
from agentclaw.community.core.service_bot.services.publish_flow.release_stage import (
    ONLINE_SPEC,
    VERIFY_SPEC,
    ReleaseStageRunner,
)
from agentclaw.community.log import get_logger


if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.core.service_bot.services.deploy.teclaw_file_promotion import (
        TeclawFilePromotion,
    )
    from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher
    from agentclaw.community.core.channel.services.engine_overrides_reader import (
        ChannelEngineOverridesReader,
    )


logger = get_logger()


class PublishFlowService(
    ProgressSyncMixin,
    RestartMixin,
    ScaleMixin,
    StageStatusMixin,
    RollbackOpsMixin,
    BaasPublishOpsMixin,
    EvalPublishMixin,
):
    """Bot 发布流程处理服务.

    职责：
    - 根据发布单状态判断当前阶段
    - 协调 BotBuildService 和 BotPublishService 完成发布流程
    - 管理状态流转

    状态流转：
    - DRAFT -> BUILDING -> BUILT (构建阶段)
    - BUILT -> VALIDATE_PUB -> VALIDATING (验证环境发布阶段)
    - VALIDATING -> ONLINE_PUB -> SUCCESS (线上发布阶段)
    - 任意状态 -> FAILED (失败)
    """

    @inject
    def __init__(
        self,
        bot_publish_service: BotPublishService,
        bot_build_service: BotBuildService,
        baas_service: BaasService,
        bot_service: "BotService",
        producer_router: DeployArtifactProducerRouter,
        common_config_service: CommonConfigService,
        *,
        resolver: "DeviceContextResolver",
        device_fs_dispatcher: "DeviceFilesystemDispatcher",
        teclaw_file_promotion: "TeclawFilePromotion",
        device_binding_repo: "DeviceBindingRepository",
        channel_overrides_reader: "ChannelEngineOverridesReader",
    ):
        """初始化流程处理服务.

        Args:
            bot_publish_service: 发布单管理服务
            bot_build_service: Bot 构建服务
            baas_service: BaaS 层 API 服务
            bot_service: Bot 管理服务（用于构建/发布阶段获取 bot 信息）
            producer_router: 按 ``device_provider`` 选择 build 产物生产者的路由。
                由 DI 根装配（`service_bot_module`）——当前仅含 ARCA（arca/baas →
                既有 ``build()``，行为等价），external/teclaw 生产者随 ConfigComposer
                collector 的 DI 落地后注册（Group C/D 后续）。
        """
        self._publish_service = bot_publish_service
        self._build_service = bot_build_service
        self._baas_service = baas_service
        self._bot_service = bot_service
        # Single build path: always go through the producer router. The DI root
        # owns router assembly — no in-class default.
        self._producer_router = producer_router
        self._common_config_service = common_config_service
        # Refreshes a teclaw binding's status read handle (device_props
        # publish_id) on republish/restart. Always DI-provided.
        self._device_binding_repo = device_binding_repo
        # teclaw promotion: at build, snapshot the running source container's
        # files (resolver+dispatcher → device_fs) into OSS and embed the refs in
        # the composed artifact. Always DI-provided.
        self._resolver = resolver
        self._device_fs_dispatcher = device_fs_dispatcher
        self._teclaw_file_promotion = teclaw_file_promotion
        # Per-stage engine_overrides (DingTalk channels): at each verify/online stage
        # action the flow re-derives that stage's channels from DB and overlays them
        # on the shared build artifact. Always DI-provided.
        self._channel_overrides_reader = channel_overrides_reader

        # Provider-behavior seam: the deploy-time steps that vary by container
        # (post-build file staging, post-upgrade MCP refresh, scale support,
        # destroy-verify-on-online) are selected by ``device_provider`` instead of
        # inline ``== teclaw`` branches. Assembled from the already-injected
        # collaborators; ``resolve_container_provider`` maps a bot to the key.
        self._provider_behaviors = ProviderBehaviorRouter(
            {
                TECLAW_DEVICE_PROVIDER: TeclawProviderBehavior(
                    build_service=bot_build_service,
                    resolver=resolver,
                    device_fs_dispatcher=device_fs_dispatcher,
                    teclaw_file_promotion=teclaw_file_promotion,
                ),
                "arca": DefaultProviderBehavior(),
                "baas": DefaultProviderBehavior(),
            },
            default_provider_key="baas",
        )

        # Shared ext/state helpers (record read-back, atomic status+ext writes,
        # per-stage engine_overrides composition). The runners extracted from this
        # facade read/write publish records through this one collaborator.
        self._ext_state = PublishExtState(
            bot_publish_service, channel_overrides_reader
        )

        # Stage-parameterized release runner: one first-release + one upgrade
        # implementation for both verify and online (was four near-duplicate
        # methods). Operates through this facade for the shared helpers.
        self._release_runner = ReleaseStageRunner(self)
        self._build_stage_runner = BuildStageRunner(self)

    def _stage_overrides(
        self, publish_record: BotPublishRecord, stage: PublishStage
    ) -> dict | None:
        return self._ext_state.stage_overrides(publish_record, stage)

    @staticmethod
    def _artifact_for_stage(
        config_artifact: dict | None,
        stage: PublishStage,
        overrides: dict | None,
    ) -> dict | None:
        return PublishExtState.artifact_for_stage(config_artifact, stage, overrides)

    @staticmethod
    def _store_stage_overrides(
        ext: dict, stage: PublishStage, overrides: dict | None
    ) -> None:
        PublishExtState.store_stage_overrides(ext, stage, overrides)

    def _refresh_publish_handle(self, binding_id, publish_id) -> None:
        """Refresh the baas ``publish_id`` stashed in a reused binding's
        ``device_props`` — the teclaw status read handle. Merge-preserving and
        best-effort; no-op without ids. Never breaks publish/restart (the read
        handle is non-critical bookkeeping)."""
        if not binding_id or publish_id is None:
            return
        try:
            self._device_binding_repo.update_device_props(
                binding_id=binding_id, props={"publish_id": publish_id}
            )
        except Exception as e:
            logger.warning(
                "[PublishFlowService._refresh_publish_handle] failed for "
                "binding_id=%s publish_id=%s: %s",
                binding_id,
                publish_id,
                e,
            )

    async def process(
        self,
        publish_id: int,
        operator: str,
    ) -> PublishFlowResult:
        """推进发布流程.

        根据发布单当前状态执行对应的阶段逻辑：
        - DRAFT: 执行完整验证环境发布流程 (_execute_full_verify_flow: 构建 + 发布)
        - BUILT: 执行验证环境发布阶段 (_execute_verify_release_phase)
        - VALIDATING: 执行线上发布阶段 (_execute_release_phase)
        - BUILDING: 返回构建进行中
        - VALIDATE_PUB: 返回验证发布进行中
        - ONLINE_PUB: 返回线上发布进行中
        - SUCCESS: 返回发布完成
        - FAILED: 返回失败信息

        Args:
            publish_id: 发布单 ID
            operator: 操作者 ID

        Returns:
            PublishFlowResult: 发布结果
        """
        logger.info(
            f"[PublishFlowService.process] called: publish_id={publish_id}, operator={operator}"
        )

        # Step 1: 查询发布单
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)

        logger.info(f"[PublishFlowService] Current status: {current_status}")

        # Step 2: 根据状态判断当前阶段并执行
        if current_status == PublishStatus.DRAFT:
            # 异步执行完整验证发布流程，不等待完成
            asyncio.create_task(
                self._execute_full_verify_flow_async(publish_record, operator)
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.BUILDING,
                message="构建已启动，请稍后查询进度",
                action="process",
            )

        elif current_status == PublishStatus.BUILT:
            return await self._execute_verify_release_phase(publish_record, operator)

        elif current_status == PublishStatus.VALIDATING:
            return await self._execute_release_phase(publish_record, operator)

        elif current_status == PublishStatus.BUILDING:
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message="构建进行中，请等待",
                action="process",
            )

        elif current_status == PublishStatus.VALIDATE_PUB:
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message="验证环境发布进行中，请等待",
                action="process",
            )

        elif current_status == PublishStatus.ONLINE_PUB:
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message="线上发布进行中，请等待",
                action="process",
            )

        elif current_status == PublishStatus.SUCCESS:
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message="发布已完成",
                action="process",
            )

        elif current_status == PublishStatus.FAILED:
            error_message = None
            if publish_record.ext:
                error_message = publish_record.ext.get("error_message")
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"发布失败: {error_message or '未知错误'}",
                action="process",
            )

        else:
            raise PublishStatusInvalidError(f"Unknown publish status: {current_status}")

    async def _execute_full_verify_flow_async(
        self,
        publish_record: BotPublishRecord,
        operator: str,
    ) -> None:
        """异步执行完整的验证环境发布流程（构建 + 发布）。

        该方法设计为后台任务执行，不返回结果。
        异常会被捕获并记录到日志，同时更新发布单状态为 FAILED。

        流程：
        1. 执行构建阶段 (_execute_build_phase)
        2. 构建成功后，执行验证环境发布 (_execute_verify_release_phase)

        Args:
            publish_record: 发布单
            operator: 操作者
        """
        publish_id = publish_record.id

        owner_id = self._get_owner_id(publish_record)
        logger.info(
            f"[PublishFlowService._execute_full_verify_flow_async] "
            f"Starting full verify flow: publish_id={publish_id}, operator={operator}, owner_id={owner_id}"
        )

        try:
            # Step 1: 执行构建阶段
            build_result = await self._execute_build_phase(publish_record, operator)

            # 检查构建是否成功
            if build_result.status != PublishStatus.BUILT:
                logger.warning(
                    f"[PublishFlowService._execute_full_verify_flow_async] "
                    f"Build phase failed or not completed: status={build_result.status}"
                )
                return

            # Step 2: 重新获取发布单（状态已更新为 BUILT）
            updated_record = self._publish_service.get_publish_by_id(publish_id)
            if not updated_record:
                raise PublishNotFoundError(f"Publish order not found after build: {publish_id}")

            # Step 3: 执行验证环境发布阶段
            release_result = await self._execute_verify_release_phase(updated_record, operator)

            logger.info(
                f"[PublishFlowService._execute_full_verify_flow_async] "
                f"Full verify flow completed: publish_id={publish_id}, status={release_result.status}"
            )

        except Exception as e:
            error_message = str(e)
            logger.error(
                f"[PublishFlowService._execute_full_verify_flow_async] "
                f"Full verify flow failed: publish_id={publish_id}, error={error_message}"
            )
            # 只更新 ext 属性，不更新状态
            try:
                def _mutate(ext: dict) -> None:
                    self._clear_retry_flag(ext)
                    ext["error_message"] = error_message

                self._merge_and_update_ext(
                    publish_id=publish_id,
                    mutator=_mutate,
                )
            except Exception as update_error:
                logger.error(
                    f"[PublishFlowService._execute_full_verify_flow_async] "
                    f"Failed to update ext: publish_id={publish_id}, error={update_error}"
                )

    def _provider_behavior(self, bot: dict):
        """The :class:`ProviderBehavior` for ``bot``'s container, resolved via the
        same ``resolve_container_provider`` mapping used for producer selection."""
        return self._provider_behaviors.resolve(
            self._baas_service.resolve_container_provider(bot)
        )

    async def _execute_build_phase(
        self,
        publish_record: BotPublishRecord,
        operator: str,
    ) -> PublishFlowResult:
        """执行构建阶段（DRAFT → BUILDING → BUILT）。委托给 BuildStageRunner。"""
        return await self._build_stage_runner.run(publish_record, operator)

    async def _execute_verify_release_phase(
        self,
        publish_record: BotPublishRecord,
        operator: str,
    ) -> PublishFlowResult:
        """执行验证环境发布阶段。

        根据验证环境是否已有 Bot 判断走升级还是首次发布：
        - 已有 Bot（ext.binding.verify 存在且 binding 记录有效）：调用 _execute_verify_upgrade
        - 无已有 Bot：调用 _execute_verify_first_release

        Args:
            publish_record: 发布单
            operator: 操作者

        Returns:
            PublishFlowResult: 发布结果
        """
        publish_id = publish_record.id
        bot_id = publish_record.source_bot_id
        owner_id = self._get_owner_id(publish_record)

        # 从扩展字段获取构建产物。ARCA = migration_path（挂载）；teclaw = 冻结的
        # config_artifact（非挂载投递）。二者皆无 → 尚未构建。
        migration_path = None
        config_artifact = None
        if publish_record.ext:
            migration_path = publish_record.ext.get("migration_path")
            config_artifact = publish_record.ext.get("config_artifact")

        if not migration_path and not config_artifact:
            error_msg = "构建产物路径不存在，请先执行构建"
            logger.error(f"[PublishFlowService._execute_verify_release_phase] publish_id={publish_id}, {error_msg}")
            ext = self._get_latest_ext(publish_id)
            self._clear_retry_flag(ext)
            ext["error_message"] = error_msg
            ext["source_status"] = PublishStatus.BUILT.value
            self._update_publish_status(
                publish_id=publish_id,
                target_status=PublishStatus.FAILED,
                source_status=PublishStatus.BUILT,
                ext=ext,
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.FAILED,
                message=error_msg,
            )

        try:
            logger.info(
                f"[PublishFlowService._execute_verify_release_phase] "
                f"Starting release to verify environment: publish_id={publish_id}, "
                f"bot_id={bot_id}, operator={operator}, owner_id={owner_id}"
            )

            # 获取 Bot 信息
            bot = self._bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
            if not bot:
                raise PublishFlowServiceError(f"Bot不存在: {bot_id}")

            ext = self._get_latest_ext(publish_id)

            # ========== 判断验证环境是否已有 Bot ==========
            verify_binding_id, bot_uuid = self._resolve_verify_binding(
                publish_record=publish_record,
                ext=ext,
            )
            is_upgrade = verify_binding_id is not None and bot_uuid is not None

            if is_upgrade:
                return await self._execute_verify_upgrade(
                    publish_record=publish_record,
                    operator=operator,
                    migration_path=migration_path,
                    bot=bot,
                    bot_uuid=bot_uuid,
                    verify_binding_id=verify_binding_id,
                )
            else:
                return await self._execute_verify_first_release(
                    publish_record=publish_record,
                    operator=operator,
                    migration_path=migration_path,
                    bot=bot,
                )

        except Exception as e:
            logger.error(f"[PublishFlowService._execute_verify_release_phase] Release failed: {e}")

            # 发布失败，更新状态和错误信息
            ext = self._get_latest_ext(publish_id)
            self._clear_retry_flag(ext)
            ext["error_message"] = str(e)
            ext["source_status"] = PublishStatus.BUILT.value
            self._update_publish_status(
                publish_id=publish_id,
                target_status=PublishStatus.FAILED,
                source_status=PublishStatus.BUILT,
                ext=ext,
            )

            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.FAILED,
                message=f"发布到验证环境失败: {str(e)}",
                action="process",
            )

    def _resolve_verify_binding(
        self,
        publish_record: BotPublishRecord,
        ext: dict,
    ) -> tuple[int | None, str | None]:
        """解析验证环境的 binding 信息，判断是否需要走升级路径。

        按优先级查找验证环境已有的 Bot binding：
        1. 当前发布单的 ext.binding.verify
        2. 上一个发布单（last_pub_id）的 ext.binding.verify

        Args:
            publish_record: 当前发布单
            ext: 当前发布单的扩展字段

        Returns:
            tuple[int | None, str | None]: (verify_binding_id, bot_uuid)
                - 如果找到有效 binding，返回 (binding_id, bot_uuid)
                - 如果未找到，返回 (None, None)，表示需要走首次发布
        """
        publish_id = publish_record.id

        # 优先级1：当前发布单的 ext.binding.verify
        current_verify_binding_id = ext.get("binding", {}).get(PublishStage.VERIFY.value)
        if current_verify_binding_id:
            binding = self._publish_service.get_device_binding_by_id(current_verify_binding_id)
            if binding and binding.device_id:
                logger.info(
                    f"[PublishFlowService._resolve_verify_binding] "
                    f"Verify Bot exists in current record: publish_id={publish_id}, "
                    f"bot_uuid={binding.device_id}, binding_id={current_verify_binding_id}"
                )
                return current_verify_binding_id, binding.device_id

        # 优先级2：从上一个发布单的 ext.binding.verify 获取（升级发布场景）
        if not publish_record.last_pub_id or publish_record.last_pub_id <= 0:
            logger.info(
                f"[PublishFlowService._resolve_verify_binding] "
                f"No verify binding found, will do first release: publish_id={publish_id}"
            )
            return None, None

        last_publish = self._publish_service.get_publish_by_id(publish_record.last_pub_id)
        if not last_publish:
            logger.info(
                f"[PublishFlowService._resolve_verify_binding] "
                f"No verify binding found, will do first release: publish_id={publish_id}"
            )
            return None, None

        last_ext = last_publish.ext or {}
        last_verify_binding_id = last_ext.get("binding", {}).get(PublishStage.VERIFY.value)
        if not last_verify_binding_id:
            logger.info(
                f"[PublishFlowService._resolve_verify_binding] "
                f"No verify binding found, will do first release: publish_id={publish_id}"
            )
            return None, None

        binding = self._publish_service.get_device_binding_by_id(last_verify_binding_id)
        if not binding or not binding.device_id:
            logger.info(
                f"[PublishFlowService._resolve_verify_binding] "
                f"No verify binding found, will do first release: publish_id={publish_id}"
            )
            return None, None

        logger.info(
            f"[PublishFlowService._resolve_verify_binding] "
            f"Verify Bot exists in last record (last_pub_id={publish_record.last_pub_id}): "
            f"publish_id={publish_id}, bot_uuid={binding.device_id}, "
            f"binding_id={last_verify_binding_id}"
        )
        return last_verify_binding_id, binding.device_id

    async def _execute_verify_first_release(
            self,
            publish_record: BotPublishRecord,
            operator: str,
            migration_path: str,
            bot: dict,
    ) -> PublishFlowResult:
        """执行验证环境首次发布（创建新 Bot）。委托给统一的 ReleaseStageRunner。"""
        return await self._release_runner.first_release(
            VERIFY_SPEC, publish_record, operator, migration_path, bot
        )

    async def _execute_verify_upgrade(
            self,
            publish_record: BotPublishRecord,
            operator: str,
            migration_path: str,
            bot: dict,
            bot_uuid: str,
            verify_binding_id: int,
    ) -> PublishFlowResult:
        """执行验证环境升级发布（复用已有 Bot）。委托给统一的 ReleaseStageRunner。"""
        return await self._release_runner.upgrade_release(
            VERIFY_SPEC,
            publish_record,
            operator,
            migration_path,
            bot,
            bot_uuid=bot_uuid,
            existing_binding_id=verify_binding_id,
            fallback=self._execute_verify_first_release,
        )

    async def _execute_release_phase(
        self,
        publish_record: BotPublishRecord,
        operator: str,
    ) -> PublishFlowResult:
        """执行发布阶段.

        根据 last_pub_id 判断是首次发布还是升级发布：
        - 首次发布：调用 BotBuildService.release_async() 创建新 Bot
        - 升级发布：调用 BotBuildService.upgrade_async() 更新现有 Bot

        Args:
            publish_record: 发布单
            operator: 操作者

        Returns:
            PublishFlowResult: 发布结果
        """
        publish_id = publish_record.id
        bot_id = publish_record.source_bot_id
        owner_id = self._get_owner_id(publish_record)

        # 从扩展字段获取构建产物。ARCA = migration_path（挂载）；teclaw = 冻结的
        # config_artifact（非挂载投递）。二者皆无 → 尚未构建。
        migration_path = None
        config_artifact = None
        if publish_record.ext:
            migration_path = publish_record.ext.get("migration_path")
            config_artifact = publish_record.ext.get("config_artifact")

        if not migration_path and not config_artifact:
            error_msg = "构建产物路径不存在，请先执行构建"
            logger.error(f"[PublishFlowService]{publish_id}, publish_record={publish_record},  {error_msg}")
            ext = self._get_latest_ext(publish_id)
            self._clear_retry_flag(ext)
            ext["error_message"] = error_msg
            ext["source_status"] = PublishStatus.VALIDATING.value
            self._update_publish_status(
                publish_id=publish_id,
                target_status=PublishStatus.FAILED,
                source_status=PublishStatus.VALIDATING,
                ext=ext,
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.FAILED,
                message=error_msg,
            )

        try:
            logger.info(
                f"[PublishFlowService] Starting release phase for: "
                f"publish_id={publish_id}, bot_id={bot_id}, operator={operator}, owner_id={owner_id}"
            )

            # 获取 Bot 信息
            bot = self._bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
            if not bot:
                raise PublishFlowServiceError(f"Bot不存在: {bot_id}")

            # ========== 核心逻辑：判断是否升级场景 ==========
            if self._should_upgrade_online(publish_record):
                # 升级发布：复用现有 Bot
                return await self._execute_upgrade_release(
                    publish_record=publish_record,
                    operator=operator,
                    migration_path=migration_path,
                    bot=bot,
                )
            else:
                # 首次发布：创建新 Bot
                return await self._execute_first_release(
                    publish_record=publish_record,
                    operator=operator,
                    migration_path=migration_path,
                    bot=bot,
                )

        except Exception as e:
            logger.error(f"[PublishFlowService] Release failed: {e}")

            # 发布失败，更新状态和错误信息
            ext = self._get_latest_ext(publish_id)
            self._clear_retry_flag(ext)
            ext["error_message"] = str(e)
            ext["source_status"] = PublishStatus.VALIDATING.value
            self._update_publish_status(
                publish_id=publish_id,
                target_status=PublishStatus.FAILED,
                source_status=PublishStatus.VALIDATING,
                ext=ext,
            )

            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.FAILED,
                message=f"发布失败: {str(e)}",
                action="process",
            )

    def _should_upgrade_online(self, publish_record: BotPublishRecord) -> bool:
        """判断线上发布阶段是否应走升级发布。

        升级场景需要同时满足：
        1. 当前发布单存在有效的 last_pub_id
        2. 上一个发布单存在
        3. 上一个发布单状态为 released（即 PublishStatus.SUCCESS）

        否则统一按首次发布处理，创建新的线上 Bot。
        """
        last_pub_id = publish_record.last_pub_id
        if not last_pub_id or last_pub_id <= 0:
            return False

        last_publish = self._publish_service.get_publish_by_id(last_pub_id)
        if not last_publish:
            logger.warning(
                f"[PublishFlowService._should_upgrade_online] "
                f"Last publish record not found, fallback to first release: last_pub_id={last_pub_id}"
            )
            return False

        try:
            last_status = PublishStatus(last_publish.status)
        except ValueError:
            logger.warning(
                f"[PublishFlowService._should_upgrade_online] "
                f"Invalid last publish status, fallback to first release: "
                f"last_pub_id={last_pub_id}, status={last_publish.status}"
            )
            return False

        if last_status != PublishStatus.SUCCESS and last_status != PublishStatus.RELEASED:
            logger.info(
                f"[PublishFlowService._should_upgrade_online] "
                f"Last publish is not released, fallback to first release: "
                f"last_pub_id={last_pub_id}, status={last_status}"
            )
            return False

        baas_status_result = self.get_publish_bot_status(last_pub_id, PublishStage.ONLINE)
        baas_status = baas_status_result.get("baas_bot_status")
        if baas_status == "RELEASED":
            return False

        return True

    async def _execute_first_release(
            self,
            publish_record: BotPublishRecord,
            operator: str,
            migration_path: str,
            bot: dict,
    ) -> PublishFlowResult:
        """执行线上首次发布（创建新 Bot）。委托给统一的 ReleaseStageRunner。"""
        return await self._release_runner.first_release(
            ONLINE_SPEC, publish_record, operator, migration_path, bot
        )

    async def _execute_upgrade_release(
            self,
            publish_record: BotPublishRecord,
            operator: str,
            migration_path: str,
            bot: dict,
    ) -> PublishFlowResult:
        """执行线上升级发布（复用现有 BaaS Bot）。

        先从 last_pub_id 解析线上 binding / bot_uuid（线上专有），再委托给统一的
        ReleaseStageRunner.upgrade_release。
        """
        publish_id = publish_record.id
        last_pub_id = publish_record.last_pub_id

        # 解析上一个发布单的线上 binding → bot_uuid（复用现有 Bot，不新建记录）。
        last_publish = self._publish_service.get_publish_by_id(last_pub_id)
        if not last_publish:
            raise PublishFlowServiceError(f"上一个发布单不存在: last_pub_id={last_pub_id}")

        last_binding_info = (last_publish.ext or {}).get("binding", {})
        online_binding_id = last_binding_info.get(PublishStage.ONLINE.value)
        if not online_binding_id:
            raise PublishFlowServiceError(
                f"上一个发布单没有线上环境的绑定信息: last_pub_id={last_pub_id}"
            )

        binding = self._publish_service.get_device_binding_by_id(online_binding_id)
        if not binding:
            raise PublishFlowServiceError(f"设备绑定记录不存在: binding_id={online_binding_id}")

        bot_uuid = binding.device_id
        if not bot_uuid:
            raise PublishFlowServiceError(
                f"设备绑定记录中没有 device_id: binding_id={online_binding_id}"
            )

        logger.info(
            f"[PublishFlowService._execute_upgrade_release] "
            f"Upgrading bot: publish_id={publish_id}, last_pub_id={last_pub_id}, "
            f"bot_uuid={bot_uuid}"
        )

        return await self._release_runner.upgrade_release(
            ONLINE_SPEC,
            publish_record,
            operator,
            migration_path,
            bot,
            bot_uuid=bot_uuid,
            existing_binding_id=online_binding_id,
            fallback=self._execute_first_release,
        )

    async def retry(
        self,
        publish_id: int,
        operator: str,
    ) -> PublishFlowResult:
        """重试失败的发布流程。

        根据失败前的状态（ext.source_status）回退到对应状态，并重新推进流程：
        - building → 回退到 DRAFT，重新构建+验证发布
        - built → 回退到 BUILT，重新验证发布
        - validate_pub → 回退到 VALIDATE_PUB，调用 BaaS 重启重试
        - validating → 回退到 VALIDATING，重新执行线上发布
        - online_pub → 回退到 ONLINE_PUB，调用 BaaS 重启重试

        Args:
            publish_id: 发布单 ID
            operator: 操作者

        Returns:
            PublishFlowResult: 重试结果

        Raises:
            PublishNotFoundError: 发布单不存在
            PublishFlowServiceError: 状态不支持重试或回退失败
        """
        logger.info(
            f"[PublishFlowService.retry] called: publish_id={publish_id}, operator={operator}"
        )

        # Step 1: 查询发布单
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)

        # Step 2: 校验状态为 FAILED
        if current_status != PublishStatus.FAILED:
            raise PublishFlowServiceError(
                f"当前状态 {current_status} 不支持重试，仅 FAILED 状态可重试"
            )

        # Step 3: 从 ext 获取失败前状态
        ext = self._get_latest_ext(publish_id)
        source_status = ext.get("source_status")
        if not source_status:
            raise PublishFlowServiceError(
                f"发布单缺少失败前状态信息(source_status)，无法重试: publish_id={publish_id}"
            )

        # Step 4: 根据 source_status 确定回退目标状态和重试动作
        retry_map = {
            PublishStatus.BUILDING.value: PublishStatus.DRAFT,
            PublishStatus.BUILT.value: PublishStatus.BUILT,
            PublishStatus.VALIDATE_PUB.value: PublishStatus.VALIDATE_PUB,
            PublishStatus.VALIDATING.value: PublishStatus.VALIDATING,
            PublishStatus.ONLINE_PUB.value: PublishStatus.ONLINE_PUB,
            PublishStatus.SUCCESS.value: PublishStatus.SUCCESS,
        }

        rollback_status = retry_map.get(source_status)
        if not rollback_status:
            raise PublishFlowServiceError(
                f"不支持的重试场景: source_status={source_status}, publish_id={publish_id}"
            )

        # Step 5: 回退状态（FAILED -> rollback_status） 并设置重试标记
        ext["retry"] = True
        try:
            self._publish_service.update_publish_status_with_ext(
                publish_id = publish_id,
                target_status=rollback_status,
                ext = ext,
                source_status=PublishStatus.FAILED,
            )
        except Exception as e:
            raise PublishFlowServiceError(
                f"状态回退失败: {rollback_status.value}, error={e}"
            )

        logger.info(
            f"[PublishFlowService.retry] Status rolled back: "
            f"publish_id={publish_id}, FAILED -> {rollback_status.value}"
        )

        # Step 6: 执行重试动作
        if rollback_status in (PublishStatus.VALIDATE_PUB, PublishStatus.ONLINE_PUB, PublishStatus.SUCCESS):
            # BaaS 发布失败，调用 restart_bot 重试
            restart_result = self.restart_bot(
                publish_id=publish_id,
                operator=operator,
            )
            success = restart_result.get("success", False)
            if not success:
                self._merge_and_update_ext(
                    publish_id=publish_id,
                    mutator=self._clear_retry_flag,
                )
            return PublishFlowResult(
                publish_id=publish_id,
                status=rollback_status,
                action="restart",
                message="重试已提交（BaaS 重启）" if success else f"重试失败: {restart_result.get('message', '未知错误')}",
            )
        else:
            # DRAFT / BUILT / VALIDATING，调用 process 重新推进流程
            return await self.process(
                publish_id=publish_id,
                operator=operator,
            )

    @staticmethod
    def _restamp_ext_artifact(ext: dict, stage: PublishStage) -> None:
        PublishExtState.stamp_stage_on_stored_artifact(ext, stage)

    def _get_owner_id(self, publish_record: BotPublishRecord) -> str:
        return self._ext_state.owner_id(publish_record)

    @staticmethod
    def _clear_retry_flag(ext: dict) -> None:
        PublishExtState.clear_retry_flag(ext)

    def _get_latest_ext(self, publish_id: int) -> dict:
        return self._ext_state.get_latest_ext(publish_id)

    def _merge_and_update_ext(
        self,
        publish_id: int,
        mutator: Callable[[dict], None],
    ) -> dict:
        return self._ext_state.merge_and_update_ext(publish_id, mutator)

    def _update_publish_status(
        self,
        publish_id: int,
        target_status: PublishStatus,
        source_status: PublishStatus,
        ext: dict | None = None,
    ) -> None:
        self._ext_state.update_status(publish_id, target_status, source_status, ext)

