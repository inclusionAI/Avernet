"""Build-phase runner.

Drives ``BUILDING → BUILT`` (the caller — ``process``/retry — owns the preceding
DRAFT → BUILDING advance): pick the artifact producer by ``device_provider``,
produce (off the event loop), run provider-specific post-build file staging, and
merge the artifact pointers onto ``ext``. On any failure the record goes
``FAILED`` with ``source_status=building`` recorded for retry.

The runner takes its real dependencies explicitly (ext/state helpers, bot lookup,
provider resolution, producer routing) instead of reaching into
``PublishFlowService`` private members — it is a standalone component, not a
friend of the facade.
"""
from __future__ import annotations

import asyncio

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishStatus
from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifactProducerRouter,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.services.publish_flow.ext_state import (
    PublishExtState,
)
from agentclaw.community.core.service_bot.services.publish_flow.provider_behavior import (
    ProviderBehaviorRouter,
)
from agentclaw.community.log import get_logger

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.bot_service import BotService

logger = get_logger()


class BuildStageRunner:
    """Run the build phase for one publish record."""

    def __init__(
        self,
        *,
        ext_state: PublishExtState,
        bot_service: "BotService",
        baas_service: BaasService,
        producer_router: DeployArtifactProducerRouter,
        provider_behaviors: ProviderBehaviorRouter,
    ) -> None:
        self._ext_state = ext_state
        self._bot_service = bot_service
        self._baas_service = baas_service
        self._producer_router = producer_router
        self._provider_behaviors = provider_behaviors

    async def build(
        self,
        publish_record: BotPublishRecord,
        operator: str,
    ) -> PublishFlowResult:
        publish_id = publish_record.id
        bot_id = publish_record.source_bot_id
        version = publish_record.version or 1
        owner_id = self._ext_state.owner_id(publish_record)

        try:
            # The record is already at BUILDING: the user-driven ``process`` (or a
            # retry) owns the DRAFT -> BUILDING advance under the optimistic lock, so
            # the build runs within BUILDING and closes it out at BUILT below. A
            # crash mid-build leaves BUILDING and a task re-run simply rebuilds.
            logger.info(
                "[BuildStageRunner] Starting build: publish_id=%s, bot_id=%s, "
                "operator=%s, owner_id=%s",
                publish_id, bot_id, operator, owner_id,
            )

            bot = self._bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
            if not bot:
                raise PublishFlowServiceError(f"Bot not found: {bot_id}")

            # Select the artifact producer by device_provider: ARCA/baas → the
            # existing build(); teclaw → compose + freeze. produce_artifact is
            # synchronous, so wrap it in to_thread to reproduce build_async's
            # non-blocking semantics.
            device_provider = self._baas_service.resolve_container_provider(bot)
            producer = self._producer_router.resolve(device_provider)
            behavior = self._provider_behaviors.resolve(device_provider)
            artifact = await asyncio.to_thread(producer.produce_artifact, bot, version)

            if not artifact.success:
                raise PublishFlowServiceError(artifact.message or "Build failed")

            # Provider-specific post-build file staging (teclaw snapshots the
            # running source container's files into OSS and embeds the refs;
            # ARCA/baas mirror to ac_file already → no-op).
            await behavior.stage_build_files(
                artifact=artifact, bot=bot, bot_id=bot_id,
                owner_id=owner_id, publish_id=publish_id,
            )

            # Build succeeded: merge the artifact pointers into ext (ARCA =
            # migration_path/build_target_path; external = config_artifact/
            # content_hash/engine_ext).
            ext = self._ext_state.get_latest_ext(publish_id)
            ext.update(artifact.ext)
            self._ext_state.update_status(
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
                message="Build completed",
                action="process",
            )

        except Exception as e:
            logger.error("[BuildStageRunner] Build failed: %s", e)

            ext = self._ext_state.get_latest_ext(publish_id)
            PublishExtState.clear_retry_flag(ext)
            ext["error_message"] = str(e)
            ext["source_status"] = PublishStatus.BUILDING.value
            self._ext_state.update_status(
                publish_id=publish_id,
                target_status=PublishStatus.FAILED,
                source_status=PublishStatus.BUILDING,
                ext=ext,
            )

            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.FAILED,
                message=f"Build failed: {str(e)}",
                action="process",
            )
