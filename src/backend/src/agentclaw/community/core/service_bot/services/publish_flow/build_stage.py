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
from dataclasses import dataclass
from typing import TYPE_CHECKING

from collections.abc import Callable
from agentclaw.community.core.digital_employee.contracts import DigitalEmployeePublicationProtocol
from agentclaw.community.core.digital_employee.snapshot import capability_digest, runtime_capability_digest
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


@dataclass(frozen=True)
class BuildArtifactOnlyResult:
    """eval 构建路径的产物——不推进 ac_bot_publish 状态。

    由 ``build_artifact_only`` 返回，供调用方直接传给 eval_publish。
    ``artifact_ext`` 是完整的 producer 产出 ext dict，供 ``build()``
    做状态提交时合并写回 ac_bot_publish.ext。
    ``employee_snapshot`` 在 ``produce_artifact`` 之前捕获（若启用数字员工），
    供 ``build()`` 在构建后与当前状态比较，检测构建期间数字员工能力变化。
    """

    migration_path: str           # ARCA 路径或空串
    config_artifact: str | None   # 外部 producer 产出的 config_artifact 或 None
    docker_image: str | None      # 解析的镜像 pin
    center_skill_uuids: tuple[str, ...]
    artifact_ext: dict            # 完整的 artifact.ext，供 build() 合并
    employee_snapshot: object | None  # produce_artifact 前的数字员工快照


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
        employee_publication_provider: Callable[[], DigitalEmployeePublicationProtocol] | None = None,
    ) -> None:
        self._employee_publication_provider = employee_publication_provider
        self._ext_state = ext_state
        self._bot_service = bot_service
        self._baas_service = baas_service
        self._producer_router = producer_router
        self._provider_behaviors = provider_behaviors
        self._runtime_projector = runtime_projector
        self._runtime_layout_probe = runtime_layout_probe

    async def build_artifact_only(
        self,
        publish_record: BotPublishRecord,
    ) -> BuildArtifactOnlyResult:
        """执行构建步骤但不推进 ac_bot_publish 状态。

        覆盖 build 阶段的核心逻辑（获取 bot → runtime_projector →
        解析 provider → produce_artifact → stage_build_files），但不执行
        ext 合并、commit_built_artifact 和状态推进。DRAFT 状态不变。

        返回 ``BuildArtifactOnlyResult`` 供调用方直接传给 eval_publish。
        """
        publish_id = publish_record.id
        bot_id = publish_record.source_bot_id
        version = publish_record.version or 1
        owner_id = self._ext_state.owner_id(publish_record)

        logger.info(
            "[BuildStageRunner] Starting build_artifact_only: publish_id=%s, "
            "bot_id=%s, owner_id=%s",
            publish_id,
            bot_id,
            owner_id,
        )

        bot = self._bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
        if not bot:
            raise PublishFlowServiceError(f"Bot not found: {bot_id}")

        try:
            await self._runtime_projector.project(
                bot_id=str(bot["bot_id"]),
                owner_id=str(bot["owner_id"]),
                scope=ProjectionScope.everything(),
            )
        except Exception:
            logger.exception(
                "[BuildStageRunner] Runtime projection did not complete "
                "before build_artifact_only: bot_id=%s",
                bot_id,
            )

        device_provider = self._baas_service.resolve_container_provider(bot)
        producer = self._producer_router.resolve(device_provider)
        behavior = self._provider_behaviors.resolve(device_provider)
        layout_observation = None
        if producer.requires_runtime_layout_observation:
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

        # 在 produce_artifact 之前捕获数字员工快照（若启用），
        # 以便 build() 在构建后检测能力是否发生变化。
        employee_snapshot = None
        if self._employee_publication_provider is not None:
            employee_snapshot = await asyncio.to_thread(
                self._employee_publication_provider().capture, bot
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

        await behavior.stage_build_files(
            artifact=artifact,
            bot=bot,
            bot_id=bot_id,
            owner_id=owner_id,
            publish_id=publish_id,
        )

        # 从 artifact.ext 中提取 migration_path 和 config_artifact，
        # 不写入 ac_bot_publish.ext
        migration_path = artifact.ext.get("migration_path", "")
        config_artifact = artifact.ext.get("config_artifact")

        center_skill_uuids = tuple(
            sorted(
                {
                    ref.skill_uuid
                    for ref in exact_center_refs_from_artifact_ext(
                        artifact.ext, validate_full_artifact=False
                    )
                }
            )
        )

        logger.info(
            "[BuildStageRunner] build_artifact_only completed: "
            "publish_id=%s, provider=%s, migration_path=%s",
            publish_id,
            device_provider,
            bool(migration_path),
        )

        return BuildArtifactOnlyResult(
            migration_path=migration_path or "",
            config_artifact=config_artifact,
            docker_image=None,  # 由调用方通过 resolve_publish_image_pin 解析
            center_skill_uuids=center_skill_uuids,
            artifact_ext=artifact.ext,
            employee_snapshot=employee_snapshot,
        )

    async def build(
        self,
        publish_record: BotPublishRecord,
        operator: str,
    ) -> PublishFlowResult:
        publish_id = publish_record.id

        try:
            logger.info(
                "[BuildStageRunner] Starting build: publish_id=%s, bot_id=%s, "
                "operator=%s, owner_id=%s",
                publish_id,
                publish_record.source_bot_id,
                operator,
                self._ext_state.owner_id(publish_record),
            )

            result = await self.build_artifact_only(publish_record)

            # 阶段 B：提交 / 状态推进（BUILDING → BUILT）
            # 数字员工快照校验 — 如果构建期间数字员工能力发生变化，拒绝发布
            if result.employee_snapshot is not None:
                owner_id = self._ext_state.owner_id(publish_record)
                bot = self._bot_service.get_bot(
                    bot_id=publish_record.source_bot_id, user_id=owner_id,
                )
                employee_service = self._employee_publication_provider()
                current = await asyncio.to_thread(employee_service.capture, bot)
                if current is None or capability_digest(current) != capability_digest(result.employee_snapshot):
                    raise ValueError("数字员工能力在构建期间发生变化，请重新构建")
                frozen = await asyncio.to_thread(employee_service.capture_artifact, bot, result.artifact_ext)
                if runtime_capability_digest(frozen) != runtime_capability_digest(current):
                    raise ValueError("发布产物与当前数字员工能力不一致，请同步配置后重新构建")
                result.artifact_ext["digital_employee_snapshot"] = frozen
            ext, expected_ext = self._ext_state.get_latest_ext_snapshot(publish_id)
            ext.pop("error_code", None)
            ext.pop("error_message", None)
            ext.pop("source_status", None)
            ext.update(result.artifact_ext)
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
                "[BuildStageRunner] Build completed: publish_id=%s",
                publish_id,
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
