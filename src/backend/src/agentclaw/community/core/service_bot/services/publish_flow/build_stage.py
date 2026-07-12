"""Build-phase runner.

Drives ``DRAFT → BUILDING → BUILT``: pick the artifact producer by
``device_provider``, produce (off the event loop), run provider-specific
post-build file staging, and merge the artifact pointers onto ``ext``. On any
failure the record goes ``FAILED`` with ``source_status=building`` recorded for
retry. Operates through the ``PublishFlowService`` facade for shared helpers.
"""
from __future__ import annotations

import asyncio

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishStatus
from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class BuildStageRunner:
    """Run the build phase for one publish record via the facade."""

    def __init__(self, flow) -> None:
        self._flow = flow

    async def build(
        self,
        publish_record: BotPublishRecord,
        operator: str,
    ) -> PublishFlowResult:
        flow = self._flow
        publish_id = publish_record.id
        bot_id = publish_record.source_bot_id
        version = publish_record.version or 1
        owner_id = flow._get_owner_id(publish_record)

        try:
            flow._publish_service.update_publish_status(
                publish_id=publish_id,
                target_status=PublishStatus.BUILDING,
                source_status=PublishStatus.DRAFT,
            )

            logger.info(
                "[BuildStageRunner] Starting build: publish_id=%s, bot_id=%s, "
                "operator=%s, owner_id=%s",
                publish_id, bot_id, operator, owner_id,
            )

            bot = flow._bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
            if not bot:
                raise PublishFlowServiceError(f"Bot不存在: {bot_id}")

            # 按 device_provider 选择产物生产者：ARCA/baas → 既有 build()；teclaw →
            # compose+冻结。produce_artifact 是同步的，包进 to_thread 复刻 build_async
            # 的非阻塞语义。
            device_provider = flow._baas_service.resolve_container_provider(bot)
            producer = flow._producer_router.resolve(device_provider)
            behavior = flow._provider_behaviors.resolve(device_provider)
            artifact = await asyncio.to_thread(producer.produce_artifact, bot, version)

            if not artifact.success:
                raise PublishFlowServiceError(artifact.message or "构建失败")

            # Provider-specific post-build file staging (teclaw snapshots the
            # running source container's files into OSS and embeds the refs;
            # ARCA/baas mirror to ac_file already → no-op).
            await behavior.stage_build_files(
                artifact=artifact, bot=bot, bot_id=bot_id,
                owner_id=owner_id, publish_id=publish_id,
            )

            # 构建成功，把产物指针合并进 ext（ARCA=migration_path/build_target_path；
            # external=config_artifact/content_hash/engine_ext）。
            ext = flow._get_latest_ext(publish_id)
            ext.update(artifact.ext)
            flow._update_publish_status(
                publish_id=publish_id,
                target_status=PublishStatus.BUILT,
                source_status=PublishStatus.BUILDING,
                ext=ext,
            )

            logger.info(
                "[BuildStageRunner] Build completed: publish_id=%s, provider=%s",
                publish_id, device_provider,
            )

            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.BUILT,
                message="构建完成",
                action="process",
            )

        except Exception as e:
            logger.error("[BuildStageRunner] Build failed: %s", e)

            ext = flow._get_latest_ext(publish_id)
            flow._clear_retry_flag(ext)
            ext["error_message"] = str(e)
            ext["source_status"] = PublishStatus.BUILDING.value
            flow._update_publish_status(
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
