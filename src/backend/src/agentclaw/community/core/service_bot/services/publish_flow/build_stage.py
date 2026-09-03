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
from typing import TYPE_CHECKING

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.schemas.publish_schemas import (
    PublishFlowResult,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.service_bot.services.deploy.artifact_build_request import (
    ArtifactBuildRequest,
    ServiceArtifactBuildError,
    ServiceArtifactBuildErrorCode,
    ServiceArtifactLayoutObservation,
)
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
from agentclaw.community.core.service_bot.services.service_artifact_refs import (
    exact_center_refs_from_artifact_ext,
)
from agentclaw.community.core.skill_center.bot_runtime_projector_protocol import (
    BotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skill_center.runtime_layout_probe_service_protocol import (
    RuntimeLayoutProbeServiceProtocol,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.workspace.skill_layout import (
    runtime_layout_engine_for_bot,
)
from agentclaw.community.log import get_logger

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
        runtime_projector: BotRuntimeProjectorProtocol,
        runtime_layout_probe: RuntimeLayoutProbeServiceProtocol,
    ) -> None:
        self._ext_state = ext_state
        self._bot_service = bot_service
        self._baas_service = baas_service
        self._producer_router = producer_router
        self._provider_behaviors = provider_behaviors
        self._runtime_projector = runtime_projector
        self._runtime_layout_probe = runtime_layout_probe

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
                publish_id,
                bot_id,
                operator,
                owner_id,
            )

            bot = self._bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
            if not bot:
                raise PublishFlowServiceError(f"Bot not found: {bot_id}")

            # A new service build first converges the complete Draft runtime.
            # The projector owns Reader flush/version resolution and every
            # engine-specific application contract. Restart/scale/rollback of
            # a frozen release never enters this build path.
            try:
                await self._runtime_projector.project(
                    bot_id=str(bot["bot_id"]),
                    owner_id=str(bot["owner_id"]),
                    scope=ProjectionScope.everything(),
                )
            except Exception:
                # Draft verification is the product gate for Service Bot
                # publication. Runtime convergence remains best-effort here;
                # Artifact construction below decides Build success.
                logger.exception(
                    "[BuildStageRunner] Runtime projection did not complete "
                    "before Service Bot build: bot_id=%s",
                    bot_id,
                )

            # Select the artifact producer by device_provider: ARCA/baas → the
            # existing build(); teclaw → compose + freeze. produce_artifact is
            # synchronous, so wrap it in to_thread to reproduce build_async's
            # non-blocking semantics.
            device_provider = self._baas_service.resolve_container_provider(bot)
            producer = self._producer_router.resolve(device_provider)
            behavior = self._provider_behaviors.resolve(device_provider)
            layout_observation = None
            if producer.requires_runtime_layout_observation:
                # TODO: let BotRuntimeProjector hand off its fresh observation
                # once that Service API can do so without coupling projection
                # results to filesystem Artifact producers. Until then, one
                # read-only probe keeps the build contract explicit and current.
                runtime_engine = runtime_layout_engine_for_bot(bot)
                probe = await self._runtime_layout_probe.probe_bot(
                    bot_id=str(bot["bot_id"]),
                    user_id=str(bot["owner_id"]),
                    engine=runtime_engine,
                )
                layout_observation = ServiceArtifactLayoutObservation.from_probe(
                    probe,
                    expected_engine=runtime_engine,
                )
                logger.info(
                    "[BuildStageRunner] Runtime layout observed: bot_id=%s, "
                    "engine=%s, status=%s, center_mount=%s, reason=%s",
                    bot_id,
                    runtime_engine,
                    layout_observation.status.value,
                    layout_observation.center_mount_status,
                    layout_observation.reason,
                )
                if layout_observation.resolved_layout is not None:
                    resolved = layout_observation.resolved_layout
                    logger.debug(
                        "[BuildStageRunner] Runtime layout paths: bot_id=%s, "
                        "active_root=%s, local_root=%s, repo_root=%s, "
                        "center_root=%s",
                        bot_id,
                        resolved.active_root,
                        resolved.local_root,
                        resolved.repo_root,
                        resolved.center_root,
                    )

            request = ArtifactBuildRequest.create(
                bot=bot,
                version=version,
                layout_observation=layout_observation,
            )
            artifact = await asyncio.to_thread(producer.produce_artifact, request)

            if not artifact.success:
                raise ServiceArtifactBuildError(
                    ServiceArtifactBuildErrorCode.SNAPSHOT_INVALID,
                    artifact.message or "Service Artifact snapshot build failed",
                )

            # Provider-specific post-build file staging (teclaw snapshots the
            # running source container's files into OSS and embeds the refs;
            # ARCA/baas write the live FS the build already sees → no-op).
            await behavior.stage_build_files(
                artifact=artifact,
                bot=bot,
                bot_id=bot_id,
                owner_id=owner_id,
                publish_id=publish_id,
            )

            # Build succeeded: merge the artifact pointers into ext (ARCA =
            # migration_path/build_target_path; external = config_artifact/
            # content_hash/engine_ext).
            ext, expected_ext = self._ext_state.get_latest_ext_snapshot(publish_id)
            ext.pop("error_code", None)
            ext.pop("error_message", None)
            ext.pop("source_status", None)
            ext.update(artifact.ext)
            center_skill_uuids = tuple(
                sorted(
                    {
                        ref.skill_uuid
                        for ref in exact_center_refs_from_artifact_ext(
                            ext, validate_full_artifact=False
                        )
                    }
                )
            )
            self._ext_state.commit_built_artifact(
                publish_id=publish_id,
                ext=ext,
                expected_ext=expected_ext,
                center_skill_uuids=center_skill_uuids,
                env=publish_record.env,
            )

            logger.info(
                "[BuildStageRunner] Build completed: publish_id=%s, provider=%s",
                publish_id,
                device_provider,
            )

            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.BUILT,
                message="Build completed",
                action="process",
            )

        except Exception as e:
            logger.exception("[BuildStageRunner] Build failed: %s", e)

            ext, expected_ext = self._ext_state.get_latest_ext_snapshot(publish_id)
            PublishExtState.clear_retry_flag(ext)
            if isinstance(e, ServiceArtifactBuildError):
                ext["error_code"] = e.code.value
                error_message = str(e)
            else:
                ext.pop("error_code", None)
                # Preserve the existing Legacy BFF diagnostic contract. The
                # public OpenAPI facade independently sanitizes deployment
                # failures before returning them to external callers.
                error_message = str(e)
            ext["error_message"] = error_message
            ext["source_status"] = PublishStatus.BUILDING.value
            self._ext_state.update_status(
                publish_id=publish_id,
                target_status=PublishStatus.FAILED,
                source_status=PublishStatus.BUILDING,
                ext=ext,
                expected_ext=expected_ext,
            )

            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.FAILED,
                message=f"Build failed: {error_message}",
                action="process",
            )
