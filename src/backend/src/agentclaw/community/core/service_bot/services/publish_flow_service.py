"""Bot 发布流程处理服务。

根据发布单状态推进发布流程的不同阶段。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, Dict, TYPE_CHECKING

from injector import inject

from agentclaw.community.core.devices.models import DeviceBindingStatus
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
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

DEVICE_COUNT_CONFIG_BUSINESS_CODE = "service_bot_device_count"
DEVICE_COUNT_DEFAULT_PARAM_CODE = "default"


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

# ``PublishFlowServiceError`` now lives in ``publish_flow.errors`` and is imported
# above; it stays importable from this module for backward compatibility
# (services/__init__.py, router_publish.py, tests).


class PublishFlowService:
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
        """执行构建阶段。

        流程：
        1. 更新状态为 BUILDING
        2. 调用 BotBuildService.build_async() 执行构建（异步）
        3. 更新状态为 BUILT，记录构建目录

        Args:
            publish_record: 发布单
            operator: 操作者

        Returns:
            PublishFlowResult: 构建结果
        """
        publish_id = publish_record.id
        bot_id = publish_record.source_bot_id
        version = publish_record.version or 1
        owner_id = self._get_owner_id(publish_record)

        try:
            # 更新状态为构建中
            self._publish_service.update_publish_status(
                publish_id=publish_id,
                target_status=PublishStatus.BUILDING,
                source_status=PublishStatus.DRAFT,
            )

            logger.info(
                f"[PublishFlowService._execute_build_phase] Starting build: "
                f"publish_id={publish_id}, bot_id={bot_id}, operator={operator}, owner_id={owner_id}"
            )

            # 获取 Bot 信息
            bot = self._bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
            if not bot:
                raise PublishFlowServiceError(f"Bot不存在: {bot_id}")

            # 按 device_provider 选择产物生产者：ARCA/baas → 既有 build()（行为等价）；
            # teclaw → compose+冻结。容器由 baas 决定（非 engine）——查 baas 取源 bot
            # 的容器 provider_type 映射成 device_provider；baas 不知该 bot（如未经
            # baas 备容器的 ARCA 草稿）则回退 baas。produce_artifact 是同步的，包进
            # to_thread 复刻 build_async 的非阻塞语义。
            device_provider = self._baas_service.resolve_container_provider(bot)
            producer = self._producer_router.resolve(device_provider)
            behavior = self._provider_behaviors.resolve(device_provider)
            artifact = await asyncio.to_thread(
                producer.produce_artifact, bot, version
            )

            if not artifact.success:
                raise PublishFlowServiceError(artifact.message or "构建失败")

            # Provider-specific post-build file staging (teclaw snapshots the
            # running source container's /workspace + /identity into OSS and embeds
            # the refs; ARCA/baas mirror to ac_file already → no-op).
            await behavior.stage_build_files(
                artifact=artifact, bot=bot, bot_id=bot_id,
                owner_id=owner_id, publish_id=publish_id,
            )

            # 构建成功，把产物指针合并进 ext。ARCA 产 migration_path/
            # build_target_path（与改造前同键同值）；external 产 config_artifact/
            # content_hash/engine_ext。verify/online 仍从同一 ext 读取。
            ext = self._get_latest_ext(publish_id)
            ext.update(artifact.ext)

            # 更新状态为构建成功
            self._update_publish_status(
                publish_id=publish_id,
                target_status=PublishStatus.BUILT,
                source_status=PublishStatus.BUILDING,
                ext=ext,
            )

            logger.info(
                f"[PublishFlowService._execute_build_phase] Build completed: "
                f"publish_id={publish_id}, provider={device_provider}"
            )

            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.BUILT,
                message="构建完成",
                action="process",
            )

        except Exception as e:
            logger.error(f"[PublishFlowService._execute_build_phase] Build failed: {e}")

            # 构建失败，更新状态和错误信息
            ext = self._get_latest_ext(publish_id)
            self._clear_retry_flag(ext)
            ext["error_message"] = str(e)
            ext["source_status"] = PublishStatus.BUILDING.value
            self._update_publish_status(
                publish_id=publish_id,
                target_status=PublishStatus.FAILED,
                source_status=PublishStatus.BUILDING,
                ext=ext,
            )

            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.FAILED,
                message=f"构建失败: {str(e)}",
                action="process",
            )

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
        """执行验证环境首次发布（创建新 Bot）。

        Args:
            publish_record: 发布单
            operator: 操作者
            migration_path: 构建产物路径
            bot: Bot 信息字典

        Returns:
            PublishFlowResult: 发布结果
        """
        publish_id = publish_record.id
        owner_id = self._get_owner_id(publish_record)

        # 首次：创建新 Bot。teclaw 走非挂载投递（build 冻结的产物 config_artifact，
        # 由 release_async 按容器 provider 路由到 create_teclaw_bot）。投递前把
        # engine_ext.stage 重盖为 canary（验证环境）并叠加本阶段的 DingTalk 渠道
        # engine_overrides（从 DB 按 verify 重取）；ARCA 无 config_artifact 时均 no-op。
        verify_overrides = self._stage_overrides(publish_record, PublishStage.VERIFY)
        config_artifact = self._artifact_for_stage(
            (publish_record.ext or {}).get("config_artifact"),
            PublishStage.VERIFY,
            verify_overrides,
        )
        release_result = await self._build_service.release_async(
            bot=bot,
            user_id=owner_id,
            migration_path=migration_path,
            device_count=1,
            publish_stage=PublishStage.VERIFY,
            config_artifact=config_artifact,
        )

        bot_uuid = release_result.get("bot_uuid")
        baas_publish_id = release_result.get("publish_id")
        if not bot_uuid:
            raise PublishFlowServiceError("BaaS 层未返回 bot_uuid")

        # 记录发布结果：创建 device binding 并更新状态
        ext = publish_record.ext or {}
        binding_id, ext = self._record_release_result(
            publish_id=publish_id,
            bot=bot,
            bot_uuid=bot_uuid,
            baas_publish_id=baas_publish_id,
            operator=operator,
            ext=ext,
            stage=PublishStage.VERIFY,
            source_status=PublishStatus.BUILT,
            target_status=PublishStatus.VALIDATE_PUB,
            engine_overrides=verify_overrides,
        )

        logger.info(
            f"[PublishFlowService._execute_verify_first_release] "
            f"Release to verify environment completed: bot_uuid={bot_uuid}"
        )

        return PublishFlowResult(
            publish_id=publish_id,
            status=PublishStatus.VALIDATE_PUB,
            message="已发布到验证环境",
            action="process",
            bot_uuid=bot_uuid,
            baas_publish_id=str(baas_publish_id) if baas_publish_id else None,
            device_binding_id=binding_id,
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
        """执行验证环境升级发布（复用已有 Bot）。

        Args:
            publish_record: 发布单
            operator: 操作者
            migration_path: 构建产物路径
            bot: Bot 信息字典
            bot_uuid: 已有 Bot 的 UUID
            verify_binding_id: 验证环境的 device binding ID

        Returns:
            PublishFlowResult: 发布结果
        """
        publish_id = publish_record.id
        version = f"{publish_record.version}"
        owner_id = self._get_owner_id(publish_record)

        # 升级：复用已有 Bot。teclaw 走非挂载重投递（update_teclaw_bot），由
        # upgrade_async 按容器 provider 路由。投递前把 engine_ext.stage 重盖为
        # canary（验证环境）并叠加本阶段的 DingTalk 渠道 engine_overrides（从 DB 按
        # verify 重取）；ARCA 无 config_artifact 时均 no-op。
        verify_overrides = self._stage_overrides(publish_record, PublishStage.VERIFY)
        config_artifact = self._artifact_for_stage(
            (publish_record.ext or {}).get("config_artifact"),
            PublishStage.VERIFY,
            verify_overrides,
        )
        upgrade_result = await self._build_service.upgrade_async(
            bot_uuid=bot_uuid,
            bot=bot,
            user_id=owner_id,
            migration_path=migration_path,
            device_count = 1,
            publish_stage=PublishStage.VERIFY,
            version=version,
            config_artifact=config_artifact,
        )

        if upgrade_result.get("success") is False and upgrade_result.get("error_code") == "BOT_NOT_FOUND":
            logger.warning(
                f"[PublishFlowService._execute_verify_upgrade] "
                f"Upgrade target bot not found, fallback to first verify release: "
                f"publish_id={publish_id}, bot_uuid={bot_uuid}, verify_binding_id={verify_binding_id}"
            )
            return await self._execute_verify_first_release(
                publish_record=publish_record,
                operator=operator,
                migration_path=migration_path,
                bot=bot,
            )

        baas_publish_id = upgrade_result.get("publish_id")
        if not baas_publish_id:
            raise PublishFlowServiceError("BaaS 层升级未返回 publish_id")

        # 复用已有 binding，更新 ext
        ext = self._get_latest_ext(publish_id)
        if "binding" not in ext:
            ext["binding"] = {}
        ext["binding"][PublishStage.VERIFY.value] = verify_binding_id
        if "publish" not in ext:
            ext["publish"] = {}
        ext["publish"][PublishStage.VERIFY.value] = baas_publish_id

        # 把 stage=canary 持久化进存储的 config_artifact 快照（ARCA 无产物时 no-op）。
        self._restamp_ext_artifact(ext, PublishStage.VERIFY)

        # Persist this stage's engine_overrides (channels) for restart/redeliver.
        self._store_stage_overrides(ext, PublishStage.VERIFY, verify_overrides)

        # Refresh the reused binding's teclaw status read handle to this new
        # publish workflow (no-op for non-teclaw; best-effort).
        self._refresh_publish_handle(
            verify_binding_id, baas_publish_id
        )

        self._update_publish_status(
            publish_id=publish_id,
            target_status=PublishStatus.VALIDATE_PUB,
            source_status=PublishStatus.BUILT,
            ext=ext,
        )

        # 审批 BaaS 发布单
        request_id = self._build_service.generate_request_id(
            bot=bot,
            publish_stage="upgrade_verify",
        )
        approved = self._approve_baas_publish(
            baas_publish_id=baas_publish_id,
            operator=operator,
            stage=PublishStage.VERIFY,
            request_id=request_id,
        )
        if approved is True:
            # Provider-specific post-upgrade refresh: an ARCA upgrade rebuilds the
            # container and refreshes MCP auth via its startup callback; a teclaw
            # upgrade has no callback, so its behavior re-pushes the outbound rule.
            self._provider_behavior(bot).refresh_after_upgrade(
                bot_uuid=bot_uuid,
                bot=bot,
            )

        logger.info(
            f"[PublishFlowService._execute_verify_upgrade] "
            f"Upgrade to verify environment completed: bot_uuid={bot_uuid}"
        )

        return PublishFlowResult(
            publish_id=publish_id,
            status=PublishStatus.VALIDATE_PUB,
            message="已升级发布到验证环境",
            action="process",
            bot_uuid=bot_uuid,
            baas_publish_id=str(baas_publish_id) if baas_publish_id else None,
            device_binding_id=verify_binding_id,
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
            if self._should_execute_upgrade_release(publish_record):
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

    def _should_execute_upgrade_release(self, publish_record: BotPublishRecord) -> bool:
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
                f"[PublishFlowService._should_execute_upgrade_release] "
                f"Last publish record not found, fallback to first release: last_pub_id={last_pub_id}"
            )
            return False

        try:
            last_status = PublishStatus(last_publish.status)
        except ValueError:
            logger.warning(
                f"[PublishFlowService._should_execute_upgrade_release] "
                f"Invalid last publish status, fallback to first release: "
                f"last_pub_id={last_pub_id}, status={last_publish.status}"
            )
            return False

        if last_status != PublishStatus.SUCCESS and last_status != PublishStatus.RELEASED:
            logger.info(
                f"[PublishFlowService._should_execute_upgrade_release] "
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
        """执行首次发布（创建新 Bot）。

        复用原有的 release 逻辑。
        """
        publish_id = publish_record.id
        version = f"{publish_record.version}"
        owner_id = self._get_owner_id(publish_record)

        # 异步执行发布到 BaaS 层（teclaw 非挂载投递冻结产物，由 release_async 路由）。
        # 投递前把 engine_ext.stage 重盖为 release（线上）并叠加本阶段的 DingTalk 渠道
        # engine_overrides（从 DB 按 online 重取）；ARCA 无产物时均 no-op。
        online_overrides = self._stage_overrides(publish_record, PublishStage.ONLINE)
        config_artifact = self._artifact_for_stage(
            (publish_record.ext or {}).get("config_artifact"),
            PublishStage.ONLINE,
            online_overrides,
        )
        release_result = await self._build_service.release_async(
            bot=bot,
            user_id=owner_id,
            migration_path=migration_path,
            device_count=1,
            publish_stage=PublishStage.ONLINE,
            version=version,
            config_artifact=config_artifact,
        )

        bot_uuid = release_result.get("bot_uuid")
        baas_publish_id = release_result.get("publish_id")
        if not bot_uuid:
            raise PublishFlowServiceError("BaaS 层未返回 bot_uuid")

        # 记录发布结果：创建 device binding 并更新状态
        ext = publish_record.ext or {}
        binding_id, ext = self._record_release_result(
            publish_id=publish_id,
            bot=bot,
            bot_uuid=bot_uuid,
            baas_publish_id=baas_publish_id,
            operator=operator,
            ext=ext,
            stage=PublishStage.ONLINE,
            source_status=PublishStatus.VALIDATING,
            target_status=PublishStatus.ONLINE_PUB,
            engine_overrides=online_overrides,
        )

        logger.info(f"[PublishFlowService._execute_first_release] Release completed: {bot_uuid}")

        return PublishFlowResult(
            publish_id=publish_id,
            status=PublishStatus.ONLINE_PUB,
            message="发布已提交",
            action="process",
            bot_uuid=bot_uuid,
            baas_publish_id=str(baas_publish_id) if baas_publish_id else None,
            device_binding_id=binding_id,
        )

    async def _execute_upgrade_release(
            self,
            publish_record: BotPublishRecord,
            operator: str,
            migration_path: str,
            bot: dict,
    ) -> PublishFlowResult:
        """执行升级发布（复用现有 BaaS Bot）。

        流程：
        1. 通过 last_pub_id 获取上一个发布单
        2. 从 ext.binding.online 获取 binding_id
        3. 通过 binding_id 获取 device_binding.device_id（即 bot_uuid）
        4. 调用 BotBuildService.upgrade_async() 升级 Bot
        5. 复用现有 device_binding，不创建新记录
        """
        publish_id = publish_record.id
        last_pub_id = publish_record.last_pub_id
        version = f"{publish_record.version}"
        owner_id = self._get_owner_id(publish_record)

        # Step 1: 获取上一个发布单
        last_publish = self._publish_service.get_publish_by_id(last_pub_id)
        if not last_publish:
            raise PublishFlowServiceError(f"上一个发布单不存在: last_pub_id={last_pub_id}")

        # Step 2: 从 ext 获取线上环境的 binding_id
        last_ext = last_publish.ext or {}
        last_binding_info = last_ext.get("binding", {})
        online_binding_id = last_binding_info.get(PublishStage.ONLINE.value)

        if not online_binding_id:
            raise PublishFlowServiceError(
                f"上一个发布单没有线上环境的绑定信息: last_pub_id={last_pub_id}"
            )

        # Step 3: 获取 device_binding，提取 bot_uuid
        binding = self._publish_service.get_device_binding_by_id(online_binding_id)
        if not binding:
            raise PublishFlowServiceError(f"设备绑定记录不存在: binding_id={online_binding_id}")

        bot_uuid = binding.device_id
        if not bot_uuid:
            raise PublishFlowServiceError(f"设备绑定记录中没有 device_id: binding_id={online_binding_id}")

        logger.info(
            f"[PublishFlowService._execute_upgrade_release] "
            f"Upgrading bot: publish_id={publish_id}, last_pub_id={last_pub_id}, "
            f"bot_uuid={bot_uuid}"
        )

        # Step 4: 调用 BotBuildService.upgrade_async 升级 Bot（teclaw 路由到
        # update_teclaw_bot，重投递冻结产物）。投递前把 engine_ext.stage 重盖为
        # release（线上）并叠加本阶段的 DingTalk 渠道 engine_overrides（从 DB 按 online
        # 重取）；ARCA 无产物时均 no-op。
        online_overrides = self._stage_overrides(publish_record, PublishStage.ONLINE)
        config_artifact = self._artifact_for_stage(
            (publish_record.ext or {}).get("config_artifact"),
            PublishStage.ONLINE,
            online_overrides,
        )
        upgrade_result = await self._build_service.upgrade_async(
            bot_uuid=bot_uuid,
            bot=bot,
            user_id=owner_id,
            device_count=1,
            migration_path=migration_path,
            publish_stage=PublishStage.ONLINE,
            version=version,
            config_artifact=config_artifact,
        )

        if upgrade_result.get("success") is False and upgrade_result.get("error_code") == "BOT_NOT_FOUND":
            logger.warning(
                f"[PublishFlowService._execute_upgrade_release] "
                f"Upgrade target bot not found, fallback to first release: "
                f"publish_id={publish_id}, last_pub_id={last_pub_id}, bot_uuid={bot_uuid}"
            )
            return await self._execute_first_release(
                publish_record=publish_record,
                operator=operator,
                migration_path=migration_path,
                bot=bot,
            )

        baas_publish_id = upgrade_result.get("publish_id")
        if not baas_publish_id:
            raise PublishFlowServiceError("BaaS 层升级未返回 publish_id")

        # Step 5: 记录发布结果 - 复用现有 binding，不创建新记录
        ext = self._get_latest_ext(publish_id)

        # 记录到 ext（复用上一个发布单的 binding_id）
        if "binding" not in ext:
            ext["binding"] = {}
        ext["binding"][PublishStage.ONLINE.value] = online_binding_id

        # 记录 BaaS 层发布单 ID
        if "publish" not in ext:
            ext["publish"] = {}
        ext["publish"][PublishStage.ONLINE.value] = baas_publish_id

        # 把 stage=release 持久化进存储的 config_artifact 快照（ARCA 无产物时 no-op）。
        self._restamp_ext_artifact(ext, PublishStage.ONLINE)

        # Persist this stage's engine_overrides (channels) for restart/redeliver.
        self._store_stage_overrides(ext, PublishStage.ONLINE, online_overrides)

        # Refresh the reused binding's teclaw status read handle to this new
        # publish workflow (no-op for non-teclaw; best-effort).
        self._refresh_publish_handle(
            online_binding_id, baas_publish_id
        )

        # 更新状态为 ONLINE_PUB
        self._update_publish_status(
            publish_id=publish_id,
            target_status=PublishStatus.ONLINE_PUB,
            source_status=PublishStatus.VALIDATING,
            ext=ext,
        )

        # 审批 BaaS 层发布单
        request_id = self._build_service.generate_request_id(
            bot=bot,
            publish_stage="upgrade_online",
        )
        approved = self._approve_baas_publish(
            baas_publish_id=baas_publish_id,
            operator=operator,
            stage=PublishStage.ONLINE,
            request_id=request_id,
        )
        if approved is True:
            # Provider-specific post-upgrade refresh: an ARCA upgrade rebuilds the
            # container and refreshes MCP auth via its startup callback; a teclaw
            # upgrade has no callback, so its behavior re-pushes the outbound rule.
            self._provider_behavior(bot).refresh_after_upgrade(
                bot_uuid=bot_uuid,
                bot=bot,
            )

        logger.info(
            f"[PublishFlowService._execute_upgrade_release] "
            f"Upgrade completed: bot_uuid={bot_uuid}, baas_publish_id={baas_publish_id}"
        )

        return PublishFlowResult(
            publish_id=publish_id,
            status=PublishStatus.ONLINE_PUB,
            message="升级发布已提交",
            action="process",
            bot_uuid=bot_uuid,
            baas_publish_id=str(baas_publish_id) if baas_publish_id else None,
            device_binding_id=online_binding_id,  # 复用现有 binding
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

    def restart_bot(
        self,
        publish_id: int,
        operator: str = "system",
    ) -> dict:
        """重启 Bot（异步执行）。

        根据发布单状态确定当前阶段，从 binding 信息获取 bot_uuid，调用 BaaS 层 upgrade 接口重新部署。
        该方法使用 asyncio.create_task 异步执行，不等待结果。

        流程：
        1. 根据 publish_id 查询发布单记录
        2. 根据发布单状态确定当前阶段（VERIFY/ONLINE）
        3. 从 ext 获取对应阶段的 binding_id
        4. 根据 binding_id 查询 device_binding 记录，获取 device_id（即 bot_uuid）
        5. 调用 BotBuildService.upgrade_async() 重新部署 Bot

        Args:
            publish_id: 发布单 ID
            operator: 操作者，默认为 "system"

        Returns:
            dict: 重启结果，包含:
                - success: 是否成功提交重启请求
                - message: 结果消息
                - stage: 发布阶段（成功时返回）
        """
        logger.info(
            f"[PublishFlowService.restart_bot] called: publish_id={publish_id}, operator={operator}"
        )

        # Step 1: 查询发布单记录
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            logger.warning(
                f"[PublishFlowService.restart_bot] Publish record not found: publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"发布单不存在: publish_id={publish_id}",
            }

        # Step 2: 根据状态确定当前阶段
        current_status = PublishStatus(publish_record.status)
        stage = self._determine_restart_stage(current_status)
        if not stage:
            logger.warning(
                f"[PublishFlowService.restart_bot] "
                f"Cannot restart for status: {current_status}, publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"当前状态 {current_status} 不支持重启操作",
                "status": current_status,
            }

        # Step 3: 重置当前阶段的BaaS重启发布单
        ext = self._get_latest_ext(publish_id)
        if "restart" in ext and stage.value in ext.get("restart", {}):
            try:
                def _mutate(latest_ext: dict) -> None:
                    restart_info = latest_ext.get("restart", {})
                    if stage.value in restart_info:
                        restart_info.pop(stage.value, None)
                    if restart_info:
                        latest_ext["restart"] = restart_info
                    else:
                        latest_ext.pop("restart", None)

                ext = self._merge_and_update_ext(
                    publish_id=publish_id,
                    mutator=_mutate,
                )
            except Exception as e:
                logger.warning(
                    f"[PublishFlowService.restart_bot] "
                    f"Failed to reset restart_publish_id: publish_id={publish_id}, error={e}"
                )
                ext = self._get_latest_ext(publish_id)

        # Step 4: 从 ext 获取对应阶段的 binding_id
        binding_info = ext.get("binding", {})
        binding_id = binding_info.get(stage.value)

        if not binding_id:
            logger.warning(
                f"[PublishFlowService.restart_bot] "
                f"No binding_id found for stage={stage.value}, publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"未找到 {stage.value} 阶段的绑定信息",
                "stage": stage.value,
            }

        # Step 5: 根据 binding_id 查询 device_binding 记录
        binding = self._publish_service.get_device_binding_by_id(binding_id)
        if not binding:
            logger.warning(
                f"[PublishFlowService.restart_bot] Device binding not found: binding_id={binding_id}"
            )
            return {
                "success": False,
                "message": f"设备绑定记录不存在: binding_id={binding_id}",
            }

        bot_uuid = binding.device_id
        if not bot_uuid:
            logger.warning(
                f"[PublishFlowService.restart_bot] No device_id in binding: binding_id={binding_id}"
            )
            return {
                "success": False,
                "message": f"设备绑定记录中没有 device_id: binding_id={binding_id}",
            }

        bot_service = self._bot_service
        bot = bot_service.get_bot(bot_id=publish_record.source_bot_id, user_id=publish_record.owner_id)
        if not bot:
            logger.warning(
                f"[PublishFlowService.restart_bot] Bot not found: bot_id={publish_record.source_bot_id}"
            )
            return {
                "success": False,
                "message": f"Bot不存在: {publish_record.source_bot_id}",
            }

        migration_path = ext.get("migration_path")
        config_artifact = ext.get("config_artifact")
        if not migration_path and not config_artifact:
            logger.warning(
                f"[PublishFlowService.restart_bot] No build artifact in ext: publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"发布单缺少构建产物: publish_id={publish_id}",
            }

        # Step 6: 异步执行重启
        asyncio.create_task(
            self._restart_bot_async(
                publish_id=publish_id,
                publish_record=publish_record,
                migration_path=migration_path,
                bot_uuid=bot_uuid,
                binding_id=binding_id,
                bot=bot,
                stage=stage,
                operator=operator,
            )
        )

        logger.info(
            f"[PublishFlowService.restart_bot] Restart task created: "
            f"publish_id={publish_id}, bot_uuid={bot_uuid}, stage={stage.value}, "
            f"operator={operator}, owner_id={publish_record.owner_id}"
        )

        return {
            "success": True,
            "message": f"重启任务已提交，阶段: {stage.value}",
            "stage": stage.value,
            "bot_uuid": bot_uuid,
        }

    async def _restart_bot_async(
        self,
        publish_id: int,
        publish_record: BotPublishRecord,
        migration_path: str,
        bot_uuid: str,
        binding_id: int,
        bot: Dict[str, Any],
        stage: PublishStage,
        operator: str,
    ) -> None:
        """异步执行 Bot 重启（通过 upgrade 接口重新部署）。

        Args:
            publish_id: 发布单 ID
            publish_record: 发布单记录
            migration_path: Bot 实例迁移后的目录路径
            bot_uuid: Bot UUID
            bot: Bot 信息字典
            stage: 发布阶段
            operator: 操作者
        """
        logger.info(
            f"[PublishFlowService._restart_bot_async] Starting restart: "
            f"publish_id={publish_id}, bot_uuid={bot_uuid}, stage={stage.value}, "
            f"operator={operator}, owner_id={publish_record.owner_id}"
        )

        try:
            # 生成 request_id（用于后续审批 BaaS 发布单）
            request_id = self._build_service.generate_request_id(
                bot=bot,
                publish_stage=f"restart_{stage.value}",
            )
            version = f"{publish_record.version}"

            # Compose the delivery artifact for THIS stage: stamp engine_ext.stage
            # to the restarted stage and overlay that stage's stored channel
            # engine_overrides (reproducing what was promoted, NOT a live re-fetch).
            # Reading the per-stage slot fixes the single-config-slot hazard — a
            # restart of a non-latest stage no longer delivers another stage's
            # channels. No stored overrides (pre-feature record) or no
            # config_artifact (ARCA) → no-ops, preserving prior behavior.
            ext = publish_record.ext or {}
            # `or {}` (not a get-default): the key may hold JSON null in a raw ext blob.
            stored_overrides = (ext.get("engine_overrides_by_stage") or {}).get(stage.value)
            config_artifact = self._artifact_for_stage(
                ext.get("config_artifact"), stage, stored_overrides
            )
            restart_result = await self._build_service.upgrade_async(
                bot_uuid=bot_uuid,
                bot=bot,
                user_id=publish_record.owner_id,
                device_count=1,
                migration_path=migration_path,
                publish_stage=stage,
                version=version,
                config_artifact=config_artifact,
            )

            if restart_result.get("success") is False and restart_result.get("error_code") == "BOT_NOT_FOUND":
                logger.warning(
                    f"[PublishFlowService._restart_bot_async] "
                    f"Restart target bot not found, fallback to first release: "
                    f"publish_id={publish_id}, bot_uuid={bot_uuid}, stage={stage.value}"
                )
                restart_result = await self._build_service.release_async(
                    bot=bot,
                    user_id=publish_record.owner_id,
                    migration_path=migration_path,
                    device_count=1,
                    publish_stage=stage,
                    version=version,
                    # teclaw: the fallback must carry the frozen artifact too,
                    # else create_teclaw_bot would receive an empty config.
                    config_artifact=config_artifact,
                )

            restart_publish_id = restart_result.get("publish_id")
            if not restart_publish_id:
                raise PublishFlowServiceError("BaaS 层重启未返回 publish_id")

            # Refresh the reused binding's teclaw status read handle to the
            # restart's publish workflow (no-op for non-teclaw; best-effort).
            self._refresh_publish_handle(
                binding_id, restart_publish_id
            )

            logger.info(
                f"[PublishFlowService._restart_bot_async] "
                f"Bot restart initiated: bot_uuid={bot_uuid}, stage={stage.value}, "
                f"publish_id={publish_id}, restart_publish_id={restart_publish_id}"
            )

            # 将 restart_publish_id 存入 ext: {"restart": {"<stage>": restart_publish_id}}
            try:
                def _mutate(ext: dict) -> None:
                    if "restart" not in ext:
                        ext["restart"] = {}
                    ext["restart"][stage.value] = restart_publish_id

                self._merge_and_update_ext(
                    publish_id=publish_id,
                    mutator=_mutate,
                )
                logger.info(
                    f"[PublishFlowService._restart_bot_async] "
                    f"Restart publish_id saved to ext: publish_id={publish_id}, "
                    f"stage={stage.value}, restart_publish_id={restart_publish_id}"
                )
            except Exception as save_error:
                logger.warning(
                    f"[PublishFlowService._restart_bot_async] "
                    f"Failed to save restart_publish_id to ext: publish_id={publish_id}, "
                    f"error={save_error}"
                )

            # 审批 BaaS 层重启发布单
            self._approve_baas_publish(
                baas_publish_id=restart_publish_id,
                operator=operator,
                stage=stage,
                request_id=request_id,
            )
            logger.info(
                f"[PublishFlowService._restart_bot_async] "
                f"Bot restart approved: bot_uuid={bot_uuid}, stage={stage.value}, "
                f"restart_publish_id={restart_publish_id}"
            )

        except Exception as e:
            logger.error(
                f"[PublishFlowService._restart_bot_async] "
                f"Failed to restart bot: publish_id={publish_id}, bot_uuid={bot_uuid}, error={e}"
            )

    def scale_bot(
        self,
        publish_id: int,
        operator: str = "system",
    ) -> dict:
        """对已发布的 service bot 发起扩缩容。

        当前仅支持线上阶段（SUCCESS / ONLINE_PUB）的 service bot。
        通过发布单线上 binding 获取 bot_uuid，然后调用 BaaS scale 接口。
        """
        logger.info(
            f"[PublishFlowService.scale_bot] called: publish_id={publish_id}, "
            f"operator={operator}"
        )

        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        owner_id = self._get_owner_id(publish_record)
        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id,
            user_id=owner_id,
        )
        if not bot:
            raise PublishFlowServiceError(f"Bot不存在: {publish_record.source_bot_id}")

        active_engine = (bot.get("active_engine") or "").strip().lower()
        if not self._provider_behavior(bot).supports_scale:
            return {
                "success": True,
                "message": "teclaw引擎的服务bot不支持扩容",
                "publish_id": publish_id,
                "engine": active_engine,
                "supported": True,
            }

        current_status = PublishStatus(publish_record.status)
        if current_status != PublishStatus.SUCCESS:
            raise PublishStatusInvalidError(
                f"当前状态 {current_status} 不支持扩容操作"
            )

        ext = self._get_latest_ext(publish_id)
        online_binding_id = (ext.get("binding") or {}).get(PublishStage.ONLINE.value)
        if not online_binding_id:
            raise PublishFlowServiceError(
                f"未找到 online 阶段的绑定信息: publish_id={publish_id}"
            )

        binding = self._publish_service.get_device_binding_by_id(online_binding_id)
        if not binding or not binding.device_id:
            raise PublishFlowServiceError(
                f"设备绑定记录不存在或缺少 device_id: binding_id={online_binding_id}"
            )

        bot_uuid = binding.device_id
        target_count = self._resolve_scale_target_count(publish_record)
        request_id = f"scale_{bot_uuid}_{int(time.time() * 1000)}"

        result = self._baas_service.scale_bot(
            bot_uuid=bot_uuid,
            owner_id=operator,
            request_id=request_id,
            target_count=target_count,
            auto_approve_publish=True,
        )

        scale_publish_id = result.get("publish_id")
        if scale_publish_id is not None:
            def _mutate(latest_ext: dict) -> None:
                latest_ext.setdefault("scale", {})["publish_id"] = scale_publish_id

            self._merge_and_update_ext(
                publish_id=publish_id,
                mutator=_mutate,
            )

        logger.info(
            f"[PublishFlowService.scale_bot] scale submitted: publish_id={publish_id}, "
            f"bot_uuid={bot_uuid}, target_count={target_count}, result={result}"
        )

        return {
            "success": True,
            "message": "扩容任务已提交",
            "publish_id": publish_id,
            "bot_uuid": bot_uuid,
            "target_count": target_count,
            "baas_publish_id": result.get("publish_id"),
            "data": result,
        }

    def _resolve_scale_target_count(self, publish_record: BotPublishRecord) -> int:
        """解析服务 Bot 扩缩容目标设备数。"""
        owner_id = self._get_owner_id(publish_record)
        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id,
            user_id=owner_id,
        )
        if not bot:
            raise PublishFlowServiceError(f"Bot不存在: {publish_record.source_bot_id}")

        ext_count = self._read_device_count_from_bot_ext(bot.get("ext"))
        if ext_count is not None:
            logger.info(
                "[PublishFlowService._resolve_scale_target_count] use bot ext device_count=%s for bot_id=%s",
                ext_count,
                publish_record.source_bot_id,
            )
            return ext_count

        default_count = self._get_default_scale_target_count()
        if default_count is not None:
            logger.info(
                "[PublishFlowService._resolve_scale_target_count] use common_config default device_count=%s for bot_id=%s",
                default_count,
                publish_record.source_bot_id,
            )
            return default_count

        raise PublishFlowServiceError(
            f"未找到 service_bot_config.device_count，且未配置默认 device_count: bot_id={publish_record.source_bot_id}"
        )

    def _read_device_count_from_bot_ext(self, bot_ext: dict[str, Any] | None) -> int | None:
        if not isinstance(bot_ext, dict):
            return None
        service_bot_config = bot_ext.get("service_bot_config")
        if not isinstance(service_bot_config, dict):
            return None
        return self._normalize_device_count(
            service_bot_config.get("device_count"),
            source="bot.ext.service_bot_config.device_count",
        )

    def _get_default_scale_target_count(self) -> int | None:
        value = self._common_config_service.get_value(
            business_code=DEVICE_COUNT_CONFIG_BUSINESS_CODE,
            param_code=DEVICE_COUNT_DEFAULT_PARAM_CODE,
            env=get_current_env(),
            default=None,
        )
        logger.info(
            "[PublishFlowService._get_default_scale_target_count] common_config value=%s",
            value,
        )
        return self._normalize_device_count(
            value,
            source="common_config.service_bot_device_count.default",
        )

    def _normalize_device_count(self, value: Any, *, source: str) -> int | None:
        if value is None or value == "":
            return None
        try:
            count = int(value)
        except (TypeError, ValueError):
            logger.warning(
                "[PublishFlowService._normalize_device_count] invalid device_count from %s: %r",
                source,
                value,
            )
            return None
        if count < 1:
            logger.warning(
                "[PublishFlowService._normalize_device_count] non-positive device_count from %s: %r",
                source,
                value,
            )
            return None
        return count

    def _determine_restart_stage(self, current_status: PublishStatus) -> PublishStage | None:
        """根据当前状态确定重启阶段。

        Args:
            current_status: 当前发布单状态

        Returns:
            发布阶段（VERIFY/ONLINE），如果不支持重启则返回 None
        """
        # VALIDATING / VALIDATE_PUB: 验证环境，重启验证环境的 bot
        if current_status in (PublishStatus.VALIDATING, PublishStatus.VALIDATE_PUB):
            return PublishStage.VERIFY
        # SUCCESS / ONLINE_PUB: 线上环境，重启线上的 bot
        elif current_status in (PublishStatus.SUCCESS, PublishStatus.ONLINE_PUB):
            return PublishStage.ONLINE
        return None

    def _determine_sync_stage(self, current_status: PublishStatus) -> PublishStage | None:
        """根据当前状态确定同步阶段。

        Args:
            current_status: 当前发布单状态

        Returns:
            发布阶段（VERIFY/ONLINE），如果不支持同步则返回 None
        """
        if current_status == PublishStatus.VALIDATE_PUB:
            return PublishStage.VERIFY
        elif current_status == PublishStatus.ONLINE_PUB:
            return PublishStage.ONLINE
        return None

    def _upgrade_last_publish(
        self,
        publish_record: BotPublishRecord,
        stage: PublishStage,
        target_status: PublishStatus,
    ) -> None:
        """更新上一个发布单状态为 UPGRADED（仅当 online 阶段成功时）。

        Args:
            publish_record: 当前发布单记录
            stage: 发布阶段（VERIFY/ONLINE）
            target_status: 目标状态
        """
        # 只有 online 阶段成功时才需要更新上一个发布单
        if stage != PublishStage.ONLINE or target_status != PublishStatus.SUCCESS:
            return

        last_pub_id = publish_record.last_pub_id
        if not last_pub_id or last_pub_id <= 0:
            return

        # 查询上一个发布单记录
        last_publish = self._publish_service.get_publish_by_id(last_pub_id)
        if not last_publish:
            logger.warning(
                f"[PublishFlowService._upgrade_last_publish] "
                f"Last publish record not found: last_pub_id={last_pub_id}"
            )
            return

        # 清除 rollback_restored_from 标记（如果存在）
        last_ext = last_publish.ext or {}
        if last_ext.pop("rollback_restored_from", None):
            logger.info(
                f"[PublishFlowService._upgrade_last_publish] "
                f"Clearing rollback_restored_from for publish {last_pub_id}"
            )

        # 更新上一个发布单状态为 UPGRADED，同时更新 ext
        try:
            self._publish_service.update_publish_status_with_ext(
                publish_id=last_pub_id,
                target_status=PublishStatus.UPGRADED,
                ext=last_ext,
                source_status=PublishStatus.SUCCESS,
            )
            logger.info(
                f"[PublishFlowService._upgrade_last_publish] "
                f"Last publish status updated to UPGRADED: last_pub_id={last_pub_id}"
            )
        except Exception as e:
            logger.warning(
                f"[PublishFlowService._upgrade_last_publish] "
                f"Failed to update last publish status: last_pub_id={last_pub_id}, error={e}"
            )

    async def execute_rollback(
        self,
        current_publish_id: int,
        target_publish_id: int,
        operator: str,
    ) -> "PublishFlowResult":
        """执行回滚部署。

        使用目标版本的配置（migration_path/config_artifact/binding）重新部署到线上。
        回滚部署在目标版本上进行，前端应同步 target_publish_id 的部署进度。

        Args:
            current_publish_id: 当前版本 ID（已变为 DRAFT，仅用于获取 owner_id 和 bot_id）
            target_publish_id: 目标版本 ID（回滚目标，已变为 SUCCESS，部署在此版本上）
            operator: 操作者

        Returns:
            PublishFlowResult: 部署结果，publish_id 为 target_publish_id
        """
        from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult

        logger.info(
            f"[PublishFlowService.execute_rollback] called: "
            f"current_publish_id={current_publish_id}, target_publish_id={target_publish_id}, "
            f"operator={operator}"
        )

        # 1. 获取目标版本记录
        target_record = self._publish_service.get_publish_by_id(target_publish_id)
        if not target_record:
            raise PublishNotFoundError(f"Target publish record not found: {target_publish_id}")

        current_record = self._publish_service.get_publish_by_id(current_publish_id)
        if not current_record:
            raise PublishNotFoundError(f"Current publish record not found: {current_publish_id}")

        # 2. 获取目标版本的构建产物
        target_ext = self._get_latest_ext(target_publish_id)
        migration_path = target_ext.get("migration_path")
        config_artifact = target_ext.get("config_artifact")

        if not migration_path and not config_artifact:
            raise PublishFlowServiceError(
                f"目标版本缺少构建产物: target_publish_id={target_publish_id}"
            )

        # 3. 从目标版本获取线上 binding
        online_binding_id = target_ext.get("binding", {}).get(PublishStage.ONLINE.value)
        if not online_binding_id:
            raise PublishFlowServiceError(
                f"目标版本缺少线上 binding: target_publish_id={target_publish_id}"
            )

        binding = self._publish_service.get_device_binding_by_id(online_binding_id)
        if not binding or not binding.device_id:
            raise PublishFlowServiceError(
                f"设备绑定记录不存在或缺少 device_id: binding_id={online_binding_id}"
            )

        bot_uuid = binding.device_id

        # 4. 获取 Bot 信息（从 current_record 获取，因为它是原始发布单）
        owner_id = current_record.owner_id
        bot = self._bot_service.get_bot(bot_id=current_record.source_bot_id, user_id=owner_id)
        if not bot:
            raise PublishFlowServiceError(f"Bot不存在: {current_record.source_bot_id}")

        # 5. 调用 BaaS upgrade 接口重新部署
        version = f"{target_record.version}"
        upgrade_result = await self._build_service.upgrade_async(
            bot_uuid=bot_uuid,
            bot=bot,
            user_id=owner_id,
            device_count=1,
            migration_path=migration_path,
            publish_stage=PublishStage.ONLINE,
            version=version,
            config_artifact=config_artifact,
        )

        baas_publish_id = upgrade_result.get("publish_id")
        if not baas_publish_id:
            raise PublishFlowServiceError("BaaS 层升级未返回 publish_id")

        # 6. 更新目标版本记录新的 BaaS 发布单 ID（回滚部署在目标版本上进行）
        if "publish" not in target_ext:
            target_ext["publish"] = {}
        target_ext["publish"][PublishStage.ONLINE.value] = baas_publish_id

        self._update_publish_status(
            publish_id=target_publish_id,
            target_status=PublishStatus.ONLINE_PUB,
            source_status=PublishStatus.SUCCESS,
            ext=target_ext,
        )

        # 7. 审批 BaaS 发布单
        request_id = self._build_service.generate_request_id(
            bot=bot,
            publish_stage="rollback",
        )
        self._approve_baas_publish(
            baas_publish_id=baas_publish_id,
            operator=operator,
            stage=PublishStage.ONLINE,
            request_id=request_id,
        )

        logger.info(
            f"[PublishFlowService.execute_rollback] Rollback deployment initiated: "
            f"current_publish_id={current_publish_id}, target_publish_id={target_publish_id}, "
            f"bot_uuid={bot_uuid}, baas_publish_id={baas_publish_id}"
        )

        return PublishFlowResult(
            publish_id=target_publish_id,
            status=PublishStatus.ONLINE_PUB,
            message="回滚发布已提交",
            action="rollback",
            target_publish_id=target_publish_id,
            bot_uuid=bot_uuid,
            baas_publish_id=str(baas_publish_id),
            device_binding_id=online_binding_id,
        )

    def _destroy_bot_by_stage(
        self,
        publish_record: BotPublishRecord,
        stage: PublishStage,
    ) -> None:
        """销毁指定阶段的 bot 实例。

        Args:
            publish_record: 发布单记录
            stage: 发布阶段（VERIFY/ONLINE）
        """
        ext = publish_record.ext or {}
        binding_info = ext.get("binding", {})
        binding_id = binding_info.get(stage.value)

        if not binding_id:
            logger.warning(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"No binding_id found for stage={stage.value}, skipping destroy"
            )
            return

        try:
            # 查询 device_binding 获取 bot_uuid
            binding = self._publish_service.get_device_binding_by_id(binding_id)
            if not binding:
                logger.warning(
                    f"[PublishFlowService._destroy_bot_by_stage] "
                    f"Device binding not found: binding_id={binding_id}"
                )
                return

            bot_uuid = binding.device_id
            if not bot_uuid:
                logger.warning(
                    f"[PublishFlowService._destroy_bot_by_stage] "
                    f"No device_id in binding: binding_id={binding_id}"
                )
                return

            logger.info(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"Destroying bot: bot_uuid={bot_uuid}, stage={stage.value}"
            )

            # 生成 request_id（销毁场景使用特殊标识）
            request_id = self._build_service.generate_request_id(
                bot={"entity_id": binding.entity_id, "entity_type": binding.entity_type, "bot_id": publish_record.source_bot_id},
                publish_stage=f"destroy_{stage.value}",
            )

            # 调用 BaaS 销毁 bot
            destroy_result = self._baas_service.stop_bot(
                bot_uuid=bot_uuid,
                operator="system",
                request_id=request_id,
            )

            destroy_publish_id = destroy_result.get("publish_id")
            logger.info(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"Bot destroy initiated: bot_uuid={bot_uuid}, stage={stage.value}, destroy_publish_id={destroy_publish_id}"
            )

            # 审批销毁流程单
            if destroy_publish_id:
                self._approve_baas_publish(
                    baas_publish_id=destroy_publish_id,
                    operator="system",
                    stage=stage,
                    request_id=request_id,
                )
                logger.info(
                    f"[PublishFlowService._destroy_bot_by_stage] "
                    f"Bot destroy approved: bot_uuid={bot_uuid}, stage={stage.value}, destroy_publish_id={destroy_publish_id}"
                )

            # 更新 device_binding 状态为 RELEASED
            self._publish_service.update_device_binding_with_props(
                binding_id=binding_id,
                status=DeviceBindingStatus.RELEASED,
                device_props={"destroy_publish_id": destroy_publish_id} if destroy_publish_id else {},
            )
            logger.info(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"Device binding status updated to RELEASED: binding_id={binding_id}"
            )

        except Exception as e:
            logger.warning(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"Failed to destroy bot: binding_id={binding_id}, stage={stage.value}, error={e}"
            )

    def _update_binding_on_success(
        self,
        ext: dict,
        stage: PublishStage,
        progress: dict,
        baas_status: str,
        baas_publish_id: int | str,
        bot_id: str,
    ) -> None:
        """更新 device_binding 为成功状态。

        Args:
            ext: 扩展字段
            stage: 发布阶段（VERIFY/ONLINE）
            progress: BaaS 发布进度信息
            baas_status: BaaS 发布状态
            baas_publish_id: BaaS 发布单 ID
            bot_id: Bot ID
        """
        # 从扩展字段获取 binding_id
        binding_info = ext.get("binding", {})
        binding_id = binding_info.get(stage.value)

        if not binding_id:
            logger.warning(
                f"[PublishFlowService._update_binding_on_success] "
                f"No binding_id found for stage={stage.value}"
            )
            return

        device_details = progress.get("device_details", [])
        device_props = {
            "bolt_id":bot_id,
            "device_details": device_details,
            "baas_status": baas_status,
            "baas_publish_id": baas_publish_id,
            "overall_progress": progress.get("overall_progress", {}),
        }

        self._publish_service.update_device_binding_with_props(
            binding_id=binding_id,
            status=DeviceBindingStatus.ACTIVE,
            device_props=device_props,
        )

        logger.info(
            f"[PublishFlowService._update_binding_on_success] "
            f"Device binding updated: binding_id={binding_id}, status=ACTIVE"
        )

    def _handle_sync_success(
        self,
        publish_id: int,
        publish_record: BotPublishRecord,
        stage: PublishStage,
        current_status: PublishStatus,
        ext: dict,
        baas_publish_id: int | str,
        progress: dict,
    ) -> PublishFlowResult:
        """处理 BaaS 发布成功。

        Args:
            publish_id: 发布单 ID
            publish_record: 发布单记录
            stage: 发布阶段（VERIFY/ONLINE）
            current_status: 当前状态
            ext: 扩展字段
            baas_publish_id: BaaS 发布单 ID
            progress: BaaS 发布进度信息

        Returns:
            PublishFlowResult: 同步结果
        """
        baas_status = progress.get("status", "")

        # 确定目标状态
        if stage == PublishStage.VERIFY:
            target_status = PublishStatus.VALIDATING
        else:
            target_status = PublishStatus.SUCCESS

        # 更新发布单扩展属性值，去掉重试标识
        ext.pop("retry", None)

        # 原子更新：同时更新状态和扩展字段，避免 update_publish_status + update_publish_ext
        # 两步操作之间的竞态条件（TOCTOU问题：先读状态再以该状态为乐观锁更新，
        # 期间状态可能被并发请求修改，导致更新失败）
        self._publish_service.update_publish_status_with_ext(
            publish_id=publish_id,
            target_status=target_status,
            ext=ext,
            source_status=current_status,
        )

        logger.info(
            f"[PublishFlowService._handle_sync_success] "
            f"Publish status updated: {current_status} -> {target_status}"
        )

        # 如果是 online 阶段成功，更新上一个发布单状态为 UPGRADED
        self._upgrade_last_publish(publish_record, stage, target_status)

        # 更新 device_binding 状态为 ACTIVE
        self._update_binding_on_success(
            ext=ext,
            stage=stage,
            progress=progress,
            baas_status=baas_status,
            baas_publish_id=baas_publish_id,
            bot_id=publish_record.source_bot_id,
        )

        if stage == PublishStage.ONLINE and current_status == PublishStatus.ONLINE_PUB.value:
            owner_id = self._get_owner_id(publish_record)
            bot = self._bot_service.get_bot(
                bot_id=publish_record.source_bot_id,
                user_id=owner_id,
            )
            if not bot:
                raise PublishFlowServiceError(
                    f"Bot不存在: {publish_record.source_bot_id}"
                )

            if not self._provider_behavior(bot).destroys_verify_bot_on_online:
                logger.info(
                    "[PublishFlowService._handle_sync_success] "
                    "Skip destroying verify BaaS bot for this provider: "
                    f"publish_id={publish_id}, bot_id={publish_record.source_bot_id}"
                )
            else:
                # Step 2: 销毁验证 BaaS 层的 bot
                try:
                    self._destroy_bot_by_stage(publish_record, PublishStage.VERIFY)
                    logger.info(
                        f"[PublishFlowService.destroy_publish_history] "
                        f"Bot destroyed: publish_id={publish_id}, stage={PublishStage.VERIFY.value}"
                    )

                except Exception as e:
                    logger.warning(
                        f"[PublishFlowService.destroy_publish_history]"
                        f"Failed to destroy BaaS bots: publish_id={publish_id}, stage={PublishStage.VERIFY.value}, error={e}"
                    )
                    # 销毁 bot 失败不阻塞整体流程

        return PublishFlowResult(
            publish_id=publish_id,
            status=target_status,
            message=f"发布进度同步成功，状态: {baas_status}",
            data=progress,
        )

    def _handle_sync_failure(
        self,
        publish_id: int,
        current_status: PublishStatus,
        ext: dict,
        progress: dict,
        error_message: str | None = None,
    ) -> PublishFlowResult:
        """处理 BaaS 发布失败。

        Args:
            publish_id: 发布单 ID
            current_status: 当前状态
            ext: 扩展字段
            progress: BaaS 发布进度信息
            error_message: 自定义错误信息，未提供时根据失败设备数量生成

        Returns:
            PublishFlowResult: 同步结果
        """
        if error_message is None:
            failed_devices = progress.get("failed_devices", [])
            error_message = f"BaaS 发布失败: {len(failed_devices)} 个设备失败"

        self._clear_retry_flag(ext)
        ext["error_message"] = error_message
        ext["source_status"] = current_status.value
        self._update_publish_status(
            publish_id=publish_id,
            target_status=PublishStatus.FAILED,
            source_status=current_status,
            ext=ext,
        )

        logger.error(f"[PublishFlowService._handle_sync_failure] {error_message}")

        return PublishFlowResult(
            publish_id=publish_id,
            status=PublishStatus.FAILED,
            message=error_message,
            data=progress,
        )

    def sync_publish_progress(
        self,
        publish_id: int,
    ) -> PublishFlowResult:
        """同步 BaaS 层发布进度并推进 AgentClaw 层发布单状态。

        根据 BaaS 层发布单状态推进 AgentClaw 发布单状态。
        当 BaaS 层状态为 ACTIVE/SUCCESS 时，更新 device_binding 状态和 device_props。

        stage 根据发布单状态自动确定：
        - VALIDATE_PUB -> verify
        - ONLINE_PUB -> release

        Args:
            publish_id: 发布单 ID

        Returns:
            PublishFlowResult: 同步结果
        """
        logger.info(f"[PublishFlowService.sync_publish_progress] Syncing: publish_id={publish_id}")

        # Step 1: 查询发布单
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        ext = publish_record.ext or {}

        # 如果是重试发布单（retry=True），且 source_status 是 VALIDATE_PUB 或 ONLINE_PUB，则直接返回重启进度

        if ext.get("retry"):
            source_status = ext.get("source_status")
            if source_status in (PublishStatus.VALIDATE_PUB.value, PublishStatus.ONLINE_PUB.value):
                logger.info(f"[PublishFlowService.sync_publish_progress] Detected retry flag with source_status={source_status}, redirecting to sync_restart_progress: publish_id={publish_id}")
                return self.sync_restart_progress(publish_id)

        current_status = PublishStatus(publish_record.status)

        # 如果是失败状态，则直接返回发布失败
        if current_status == PublishStatus.FAILED:
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"当前状态 {current_status} ，请重试！",
            )

        # 如果是building与built等状态，直接返回
        if current_status in [PublishStatus.BUILDING, PublishStatus.BUILT]:
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"当前状态 {current_status} ，请等待！",
            )

        # Step 2: 根据当前状态确定 stage
        stage = self._determine_sync_stage(current_status)
        if not stage:
            logger.warning(
                f"[PublishFlowService.sync_publish_progress] "
                f"Invalid status for sync: {current_status}, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"当前状态 {current_status} 不支持同步进度",
            )

        # Step 3: 获取 BaaS 层发布单 ID
        publish_info = ext.get("publish", {})
        baas_publish_id = publish_info.get(stage.value)
        if not baas_publish_id:
            logger.warning(
                f"[PublishFlowService.sync_publish_progress] "
                f"No baas_publish_id found for stage={stage.value}, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"未找到 {stage.value} 阶段的 BaaS 发布单 ID",
            )

        # Step 4: 调用 BaaS 层获取发布进度
        try:
            progress = self.get_baas_publish_progress(
                baas_publish_id=baas_publish_id,
            )
        except Exception as e:
            logger.error(f"[PublishFlowService.sync_publish_progress] Failed to get progress: {e}")
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"获取 BaaS 发布进度失败: {str(e)}",
            )

        baas_status = progress.get("status", "")
        logger.info(
            f"[PublishFlowService.sync_publish_progress] "
            f"BaaS status: {baas_status}, publish_id={publish_id}"
        )

        # Step 5: 根据 BaaS 状态分发处理
        if baas_status == "SUCCESS":
            return self._handle_sync_success(
                publish_id=publish_id,
                publish_record=publish_record,
                stage=stage,
                current_status=current_status,
                ext=ext,
                baas_publish_id=baas_publish_id,
                progress=progress,
            )

        elif baas_status == "FAILED":
            return self._handle_sync_failure(
                publish_id=publish_id,
                current_status=current_status,
                ext=ext,
                progress=progress,
            )

        else:
            # 其他状态（INIT, PENDING, APPROVING 等）
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"BaaS 发布状态: {baas_status}",
                data=progress,
            )

    def sync_scale_progress(
        self,
        publish_id: int,
    ) -> PublishFlowResult:
        """查询扩容发布单状态。

        从发布单 ext.scale.publish_id 获取 BaaS 层扩容发布单 ID，
        调用 BaaS 层获取发布进度并返回。

        Args:
            publish_id: 发布单 ID

        Returns:
            PublishFlowResult: 扩容进度结果
        """
        logger.info(f"[PublishFlowService.sync_scale_progress] Syncing scale progress: publish_id={publish_id}")

        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)
        ext = publish_record.ext or {}
        scale_info = ext.get("scale", {})
        scale_publish_id = scale_info.get("publish_id")

        if not scale_publish_id:
            logger.warning(
                f"[PublishFlowService.sync_scale_progress] "
                f"No scale_publish_id found, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message="未找到扩容发布单 ID",
            )

        try:
            progress = self.get_baas_publish_progress(
                baas_publish_id=scale_publish_id,
            )
        except Exception as e:
            logger.error(f"[PublishFlowService.sync_scale_progress] Failed to get scale progress: {e}")
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"获取 BaaS 扩容发布进度失败: {str(e)}",
            )

        baas_status = progress.get("status", "")
        logger.info(
            f"[PublishFlowService.sync_scale_progress] "
            f"BaaS scale status: {baas_status}, publish_id={publish_id}"
        )

        return PublishFlowResult(
            publish_id=publish_id,
            status=current_status,
            message=f"BaaS 扩容状态: {baas_status}",
            data=progress,
        )

    def sync_restart_progress(
        self,
        publish_id: int,
    ) -> PublishFlowResult:
        """查询重启发布单状态。

        根据发布单状态确定当前阶段，从 ext 中获取 BaaS 层重启发布单 ID，
        调用 BaaS 层获取发布进度并返回。

        Args:
            publish_id: 发布单 ID

        Returns:
            PublishFlowResult: 重启进度结果
        """
        logger.info(f"[PublishFlowService.sync_restart_progress] Syncing restart progress: publish_id={publish_id}")

        # Step 1: 查询发布单
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)

        # Step 2: 根据状态确定重启阶段
        stage = self._determine_restart_stage(current_status)
        if not stage:
            logger.warning(
                f"[PublishFlowService.sync_restart_progress] "
                f"Invalid status for restart sync: {current_status}, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"当前状态 {current_status} 不支持查询重启进度",
            )

        # Step 3: 从 ext 获取 BaaS 层重启发布单 ID
        ext = publish_record.ext or {}
        restart_info = ext.get("restart", {})
        restart_publish_id = restart_info.get(stage.value)

        if not restart_publish_id:
            logger.warning(
                f"[PublishFlowService.sync_restart_progress] "
                f"No restart_publish_id found for stage={stage.value}, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"未找到 {stage.value} 阶段的重启发布单 ID",
            )

        # Step 4: 调用 BaaS 层获取发布进度
        try:
            progress = self.get_baas_publish_progress(
                baas_publish_id=restart_publish_id,
            )
        except Exception as e:
            logger.error(f"[PublishFlowService.sync_restart_progress] Failed to get restart progress: {e}")
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"获取 BaaS 重启发布进度失败: {str(e)}",
            )

        baas_status = progress.get("status", "")
        logger.info(
            f"[PublishFlowService.sync_restart_progress] "
            f"BaaS restart status: {baas_status}, publish_id={publish_id}, stage={stage.value}"
        )

        # Step 5: 根据 BaaS 状态推进发布单状态
        # VALIDATING 和 SUCCESS 是已完成的稳态，不需要推进
        if current_status in (PublishStatus.VALIDATING, PublishStatus.SUCCESS):
            logger.info(
                f"[PublishFlowService.sync_restart_progress] "
                f"Current status is {current_status}, skip status update: publish_id={publish_id}"
            )

            # 如果失败，刚需要把当前发布单状态更新为失败
            if baas_status == "FAILED":
                return self._handle_sync_failure(
                    publish_id=publish_id,
                    current_status=current_status,
                    ext=ext,
                    progress=progress,
                    error_message=f"重启发布状态: {baas_status}",
                )

            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"重启发布状态: {baas_status}",
                action="sync_restart",
                data=progress,
            )

        if baas_status == "SUCCESS":
            return self._handle_sync_success(
                publish_id=publish_id,
                publish_record=publish_record,
                stage=stage,
                current_status=current_status,
                ext=ext,
                baas_publish_id=restart_publish_id,
                progress=progress,
            )

        elif baas_status == "FAILED":
            return self._handle_sync_failure(
                publish_id=publish_id,
                current_status=current_status,
                ext=ext,
                progress=progress,
            )

        else:
            # 其他状态（INIT, PENDING, APPROVING 等）
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"重启发布状态: {baas_status}",
                action="sync_restart",
                data=progress,
            )

    def get_baas_publish_progress(
        self,
        *,
        baas_publish_id: int | str,
        include_devices: bool = True,
    ) -> Dict[str, Any]:
        """查询 BaaS 发布进度。"""
        logger.info(
            f"[PublishFlowService.get_baas_publish_progress] Query BaaS progress: "
            f"baas_publish_id={baas_publish_id}, include_devices={include_devices}"
        )
        try:
            return self._baas_service.get_publish_progress(
                publish_id=int(baas_publish_id),
                include_devices=include_devices,
            )
        except Exception as e:
            logger.error(
                f"[PublishFlowService.get_baas_publish_progress] "
                f"Failed to get BaaS publish progress: baas_publish_id={baas_publish_id}, "
                f"error={e}"
            )
            raise

    def _approve_baas_publish(
        self,
        baas_publish_id: int | str,
        operator: str,
        stage: PublishStage,
        request_id: str,
    ) -> bool:
        """审批 BaaS 层发布单。

        Args:
            baas_publish_id: BaaS 层发布工作流 ID
            operator: 审批操作者用户 ID
            stage: 发布阶段，用于日志区分
            request_id: 请求 ID，用于幂等性控制
        """
        if not baas_publish_id:
            logger.warning(
                f"[PublishFlowService._approve_baas_publish] "
                f"No baas_publish_id provided, skipping approve for stage={stage.value}"
            )
            return False

        logger.info(
            f"[PublishFlowService._approve_baas_publish] "
            f"Approving BaaS publish: baas_publish_id={baas_publish_id}, stage={stage.value}"
        )

        try:
            self._baas_service.approve_publish(
                publish_id=int(baas_publish_id),
                operator=operator,
                request_id=request_id,
                comment=f"自动审批 - {stage.value}环境发布",
            )
            logger.info(
                f"[PublishFlowService._approve_baas_publish] "
                f"BaaS publish approved: baas_publish_id={baas_publish_id}, stage={stage.value}"
            )
            return True
        except Exception as e:
            logger.warning(
                f"[PublishFlowService._approve_baas_publish] "
                f"Failed to approve BaaS publish: baas_publish_id={baas_publish_id}, "
                f"stage={stage.value}, error={e}, continuing..."
            )
            return False

    def _record_release_result(
        self,
        publish_id: int,
        bot: dict,
        bot_uuid: str,
        baas_publish_id: int | str,
        operator: str,
        ext: dict,
        stage: PublishStage,
        source_status: PublishStatus,
        target_status: PublishStatus,
        engine_overrides: dict | None = None,
    ) -> tuple[int, dict]:
        """记录发布结果：创建 device binding 并更新扩展字段。

        Args:
            publish_id: 发布单 ID
            bot: Bot 信息字典
            bot_uuid: BaaS 层返回的 bot_uuid
            baas_publish_id: BaaS 层发布单 ID
            operator: 操作者
            ext: 扩展字段
            stage: 发布阶段（VERIFY/ONLINE）
            source_status: 源状态
            target_status: 目标状态

        Returns:
            tuple[int, dict]: (binding_id, 更新后的 ext)
        """
        # 创建 device binding 记录。device_provider 由 baas 决定（查源 bot 的容器
        # provider_type，teclaw→teclaw 否则 baas），替代原先写死的 "baas"；与 build
        # 阶段的生产者选择共用同一解析规则（同一 resolve_container_provider）。
        binding_id = self._publish_service.create_device_binding(
            entity_id=bot.get("entity_id", ""),
            entity_type=bot.get("entity_type", "staff"),
            device_id=bot_uuid,
            device_provider=self._baas_service.resolve_container_provider(bot),
            # Stash the baas publish_id as the teclaw status read handle (no-op
            # for non-teclaw bindings — their status reads ignore this key).
            device_props={"publish_id": baas_publish_id},
            applied_by=operator,
            apply_reason="",
            status=DeviceBindingStatus.PENDING,
        )

        ext = self._get_latest_ext(publish_id)

        # 记录 binding_id 到扩展字段
        if "binding" not in ext:
            ext["binding"] = {}
        ext["binding"][stage.value] = binding_id

        # 记录 baas 层发布单 ID 到扩展字段
        if "publish" not in ext:
            ext["publish"] = {}
        ext["publish"][stage.value] = baas_publish_id

        # 把本阶段（canary/release）持久化进存储的 config_artifact 快照。本方法在内部
        # 重新读取了 ext（覆盖调用方的修改），所以首次发布路径的 stage 持久化必须落在
        # 这里；ARCA 无 config_artifact 时 no-op。
        self._restamp_ext_artifact(ext, stage)

        # Persist this stage's engine_overrides (DingTalk channels) next to the
        # binding/publish refs. Same reason as the restamp above: this method
        # re-reads ext, so the first-release store must land here. No-op for ARCA
        # (engine_overrides is None).
        self._store_stage_overrides(ext, stage, engine_overrides)

        # 更新状态
        self._update_publish_status(
            publish_id=publish_id,
            target_status=target_status,
            source_status=source_status,
            ext=ext,
        )

        # 审批 BaaS 层发布单
        request_id = self._build_service.generate_request_id(
            bot=bot,
            publish_stage=stage.value,
        )

        self._approve_baas_publish(
            baas_publish_id=baas_publish_id,
            operator=operator,
            stage=stage,
            request_id=request_id,
        )

        logger.info(
            f"[PublishFlowService._record_release_result] "
            f"Release recorded: publish_id={publish_id}, stage={stage.value}, "
            f"binding_id={binding_id}, baas_publish_id={baas_publish_id}"
        )

        return binding_id, ext

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

    async def general_publish(
        self,
        publish_id: int,
        operator: str,
        publish_stage: PublishStage = PublishStage.EVAL,
        biz_id: str = "",
    ) -> dict:
        """发布评估环境。

        评估环境是主发布流程之外的旁支能力：
        - 不推进主发布单状态机
        - 不写入 publish.ext / binding
        - 仅复用发布单上的构建产物与 bot 基础信息
        """
        logger.info(
            f"[PublishFlowService.general_publish] Start release: "
            f"publish_id={publish_id}, operator={operator}"
        )

        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        owner_id = self._get_owner_id(publish_record)
        migration_path = (publish_record.ext or {}).get("migration_path", "")
        config_artifact = (publish_record.ext or {}).get("config_artifact")
        if not migration_path and not config_artifact:
            raise PublishFlowServiceError("构建产物路径不存在，请先执行构建")

        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id,
            user_id=owner_id,
        )
        if not bot:
            raise PublishFlowServiceError(f"Bot不存在: {publish_record.source_bot_id}")

        eval_overrides = self._stage_overrides(publish_record, publish_stage)
        eval_artifact = self._artifact_for_stage(
            config_artifact,
            publish_stage,
            eval_overrides,
        )

        ext_info = {}
        if biz_id:
            ext_info["biz_id"] = biz_id

        # 发布评估环境
        release_result = await self._build_service.release_async(
            bot=bot,
            user_id=owner_id,
            migration_path=migration_path,
            device_count=1,
            publish_stage=publish_stage,
            version=str(publish_record.version or 1),
            config_artifact=eval_artifact,
            ext_info=ext_info,
        )

        bot_uuid = release_result.get("bot_uuid")
        baas_publish_id = release_result.get("publish_id")
        if not bot_uuid:
            raise PublishFlowServiceError("评估环境发布失败: BaaS 未返回 bot_uuid")

        request_id = self._build_service.generate_request_id(
            bot=bot,
            publish_stage=publish_stage.value,
        )
        self._approve_baas_publish(
            baas_publish_id=baas_publish_id,
            operator=operator,
            stage=publish_stage,
            request_id=request_id,
        )

        result = {
            "success": True,
            "publish_id": publish_id,
            "stage": publish_stage.value,
            "bot_uuid": bot_uuid,
            "baas_publish_id": baas_publish_id,
            "baas_bot_status": release_result.get("status"),
        }
        logger.info(
            f"[PublishFlowService.general_publish] Release success: {result}"
        )
        return result

    def general_teardown(
        self,
        bot_uuid: str,
        *,
        operator: str = "system",
        request_bot: dict | None = None,
    ) -> dict:
        """销毁评估环境。

        仅依赖评估环境自身 bot_uuid；调用方后续可改为从独立评估任务表读取并传入。
        不触碰主发布单 ext/binding。
        """
        if not bot_uuid:
            raise PublishFlowServiceError("bot_uuid 不能为空")

        request_bot = request_bot or {"bot_id": bot_uuid}
        request_id = self._build_service.generate_request_id(
            bot=request_bot,
            publish_stage="destroy_eval",
        )
        destroy_result = self._baas_service.destroy_bot(
            bot_uuid=bot_uuid,
            operator=operator,
            request_id=request_id,
        )
        destroy_publish_id = destroy_result.get("publish_id")
        self._approve_baas_publish(
            baas_publish_id=destroy_publish_id,
            operator=operator,
            stage=PublishStage.EVAL,
            request_id=request_id,
        )
        result = {
            "success": True,
            "bot_uuid": bot_uuid,
            "baas_publish_id": destroy_publish_id,
            "message": "评估环境销毁已提交",
        }
        logger.info(
            f"[PublishFlowService.general_teardown] Destroy success: {result}"
        )
        return result

    def get_publish_bot_status(
        self,
        publish_id: int,
        stage: PublishStage,
    ) -> Dict[str, Any]:
        """查询指定发布单阶段对应的 BaaS bot 状态 / 详情。"""
        logger.info(
            f"[PublishFlowService.get_publish_bot_status] Start query: publish_id={publish_id}, stage={stage.value}"
        )

        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        ext = publish_record.ext or {}
        binding_info = ext.get("binding", {})
        binding_id = binding_info.get(stage.value)
        if not binding_id:
            raise PublishFlowServiceError(f"未找到 {stage.value} 阶段的绑定信息")

        binding = self._publish_service.get_device_binding_by_id(binding_id)
        if not binding:
            raise PublishFlowServiceError(f"未找到绑定记录: binding_id={binding_id}")

        bot_uuid = getattr(binding, "device_id", "")
        if not bot_uuid:
            raise PublishFlowServiceError(f"绑定记录缺少 bot_uuid: binding_id={binding_id}")

        baas_bot = self._baas_service.get_bot(bot_uuid=bot_uuid)
        result = {
            "publish_id": publish_id,
            "stage": stage.value,
            "binding_id": binding_id,
            "bot_uuid": bot_uuid,
            "baas_bot_status": baas_bot.get("status"),
        }
        logger.info(
            f"[PublishFlowService.get_publish_bot_status] Query success: {result}"
        )
        return result

    def destroy_publish_history(
        self,
        publish_id: int,
        stage: PublishStage,
    ) -> dict:
        """销毁发布历史。

        注意：调用此方法前需先将 bot 推进到相应状态：
        - 验证阶段 (VERIFY): 需先回退到草稿状态 (DRAFT)
        - 线上阶段 (ONLINE): 需先推进到下线状态 (RELEASED)

        销毁流程：
        1. 通过 publish_id 查询 BotPublishRecord 记录
        2. 调用 _destroy_bot_by_stage 方法销毁指定阶段的 BaaS 层 bot

        Args:
            publish_id: AgentClaw 层发布单 ID
            stage: 发布阶段（VERIFY/ONLINE）

        Returns:
            dict: 销毁结果，包含:
                - success: 是否成功
                - bot_destroyed: 是否销毁了 bot
                - message: 结果消息

        Raises:
            PublishNotFoundError: 发布单不存在
            PublishFlowServiceError: 销毁失败
        """
        logger.info(f"[PublishFlowService.destroy_publish_history] Starting destroy: publish_id={publish_id}, stage={stage.value}")

        # Step 1: 查询发布单
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)

        # 检查状态：只有 DRAFT 和 RELEASED 状态才能销毁
        allowed_statuses = [PublishStatus.DRAFT, PublishStatus.RELEASED]
        if current_status not in allowed_statuses:
            raise PublishFlowServiceError(
                f"发布单状态不允许销毁: 当前状态={current_status}, "
                f"仅允许状态: {[s.value for s in allowed_statuses]}"
            )

        result = {
            "success": True,
            "bot_destroyed": False,
            "message": "",
        }

        # Step 2: 销毁 BaaS 层的 bot
        try:
            self._destroy_bot_by_stage(publish_record, stage)
            result["bot_destroyed"] = True
            logger.info(
                f"[PublishFlowService.destroy_publish_history] "
                f"Bot destroyed: publish_id={publish_id}, stage={stage.value}"
            )

        except Exception as e:
            logger.warning(
                f"[PublishFlowService.destroy_publish_history]"
                f"Failed to destroy BaaS bots: publish_id={publish_id}, stage={stage.value}, error={e}"
            )
            # 销毁 bot 失败不阻塞整体流程

        result["message"] = f"发布历史销毁完成: publish_id={publish_id}, stage={stage.value}"

        logger.info(f"[PublishFlowService.destroy_publish_history] Destroy completed: {result}")

        return result
