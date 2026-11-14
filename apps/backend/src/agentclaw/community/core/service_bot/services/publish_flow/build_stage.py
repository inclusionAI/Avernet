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
from functools import partial
import threading
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
from agentclaw.community.core.service_bot.baas_service_errors import BaasNoActiveDevicesError
from agentclaw.community.core.service_bot.services.bot_publish_service import PublishNotFoundError
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
from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.bot_service import BotService

logger = get_logger()
_READINESS_SLOTS = threading.BoundedSemaphore(8)


async def _readiness_read(fn, *args, **kwargs):
    """Bound detached, read-only checks when the underlying client stalls."""
    while not _READINESS_SLOTS.acquire(blocking=False):
        await asyncio.sleep(0.05)
    loop = asyncio.get_running_loop()
    result = loop.create_future()

    def finish(value, error):
        if not result.done():
            if error is None:
                result.set_result(value)
            else:
                result.set_exception(error)

    def run():
        try:
            value, error = partial(fn, *args, **kwargs)(), None
        except Exception as exc:
            value, error = None, exc
        finally:
            _READINESS_SLOTS.release()
        try:
            loop.call_soon_threadsafe(finish, value, error)
        except RuntimeError:
            pass  # The timed-out request's event loop has already closed.

    try:
        threading.Thread(target=bind_current_avernet_tenant(run), name="draft-readiness", daemon=True).start()
    except Exception:
        _READINESS_SLOTS.release()
        raise
    return await result


class PublicationNoLongerBuilding(Exception):
    def __init__(self, status: str) -> None:
        self.status = status


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

    async def _wait_for_draft_device(self, record: BotPublishRecord, bot: dict) -> dict:
        """Wait only for the known BaaS no-active-device restart window.

        The probe is read-only and runs before any build snapshot is captured.
        Each request has a one-second command timeout and BaaS adds ten seconds
        of HTTP timeout; attempts and delay are bounded independently of the
        queue's existing deadline.
        """
        owner = self._ext_state.owner_id(record)
        read = _readiness_read

        def lookup():
            return self._bot_service.get_bot_by_address(
                bot_id=record.source_bot_id, user_id=owner,
                source_bot_pk=record.source_bot_pk,
            )

        binding = bot.get("device_binding") or {}
        if bot.get("active_engine") not in ("aicoding", "claude_code"):
            return bot
        if not bot.get("binding_id") or not binding:
            raise PublishFlowServiceError("Draft device binding is unavailable")
        provider = binding.get("device_provider")
        if provider not in ("baas", "arca", "local", "teclaw"):
            raise PublishFlowServiceError("Draft device provider is unavailable")
        if provider != "baas":
            return bot
        if binding.get("status") not in ("PENDING", "ACTIVE"):
            raise PublishFlowServiceError("Draft device binding is not available")
        target = (bot.get("id"), bot.get("owner_id"), bot.get("binding_id"), bot.get("active_engine"),
                  binding.get("device_id"), binding.get("device_provider"))
        if not all(target):
            raise PublishFlowServiceError("Draft device identity is unavailable")
        bot_uuid = (binding.get("device_props") or {}).get("bot_uuid") or binding.get("device_id")
        for attempt in range(22):
            current = await read(self._ext_state.get_record, record.id)
            if current.status != PublishStatus.BUILDING.value:
                raise PublicationNoLongerBuilding(current.status)
            if attempt:
                retry_bot = await read(lookup)
                retry_binding = retry_bot.get("device_binding") or {}
                retry_target = (retry_bot.get("id"), retry_bot.get("owner_id"),
                                retry_bot.get("binding_id"), retry_bot.get("active_engine"), retry_binding.get("device_id"),
                                retry_binding.get("device_provider"))
                retry_uuid = (retry_binding.get("device_props") or {}).get("bot_uuid") or retry_binding.get("device_id")
                if (retry_target != target or retry_uuid != bot_uuid
                        or retry_binding.get("status") not in ("PENDING", "ACTIVE")
                        or retry_bot.get("status") in ("FAILED", "RELEASED", "STOPPED")):
                    raise PublishFlowServiceError("Draft device changed during readiness check")
            try:
                result = await read(
                    self._baas_service.exec_command_on_bot,
                    bot_uuid=bot_uuid, cmd="true", timeout_seconds=1, safe_errors=True,
                )
            except BaasNoActiveDevicesError:
                if attempt == 21:
                    raise
                await asyncio.sleep(2)
                continue
            if result.get("exit_code") != 0:
                raise PublishFlowServiceError("Draft device probe failed")
            current_bot = await read(lookup)
            current_binding = current_bot.get("device_binding") or {}
            current_target = (current_bot.get("id"), current_bot.get("owner_id"),
                              current_bot.get("binding_id"), current_bot.get("active_engine"), current_binding.get("device_id"),
                              current_binding.get("device_provider"))
            current_uuid = (current_binding.get("device_props") or {}).get("bot_uuid") or current_binding.get("device_id")
            current = await read(self._ext_state.get_record, record.id)
            if current.status != PublishStatus.BUILDING.value:
                raise PublicationNoLongerBuilding(current.status)
            if (current_target != target or current_uuid != bot_uuid
                    or current_binding.get("status") not in ("PENDING", "ACTIVE")
                    or current_bot.get("status") in ("FAILED", "RELEASED", "STOPPED")):
                raise PublishFlowServiceError("Draft device or publication changed during readiness check")
            return current_bot

    async def build_artifact_only(
        self,
        publish_record: BotPublishRecord,
        *, verified_bot: dict | None = None,
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

        bot = verified_bot if verified_bot is not None else self._bot_service.get_bot_by_address(bot_id=bot_id, user_id=owner_id, source_bot_pk=publish_record.source_bot_pk)
        if not bot:
            raise PublishFlowServiceError(f"Bot not found: {bot_id}")

        # Build declares only Skill convergence. Per-domain file-backed engines
        # therefore leave Draft MCP state untouched before snapshot; whole-artifact
        # engines still recompose and deliver the complete persisted artifact.
        try:
            await self._runtime_projector.project(
                bot_id=str(bot["bot_id"]),
                owner_id=str(bot["owner_id"]),
                scope=ProjectionScope(skills=True),
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

            try:
                bot = self._bot_service.get_bot_by_address(
                    bot_id=publish_record.source_bot_id, user_id=self._ext_state.owner_id(publish_record),
                    source_bot_pk=publish_record.source_bot_pk,
                )
                verified_bot = await asyncio.wait_for(self._wait_for_draft_device(publish_record, bot), timeout=45)
            except TimeoutError:
                # Any outstanding thread performs read-only work. It cannot
                # continue the build after its awaiting coroutine is cancelled.
                raise PublishFlowServiceError("Draft device readiness timed out") from None
            result = await self.build_artifact_only(publish_record, verified_bot=verified_bot)

            # 阶段 B：提交 / 状态推进（BUILDING → BUILT）
            # 数字员工快照校验 — 如果构建期间数字员工能力发生变化，拒绝发布
            if result.employee_snapshot is not None:
                owner_id = self._ext_state.owner_id(publish_record)
                bot = self._bot_service.get_bot_by_address(
                    bot_id=publish_record.source_bot_id, user_id=owner_id,
                    source_bot_pk=publish_record.source_bot_pk,
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

        except PublicationNoLongerBuilding as e:
            return PublishFlowResult(
                publish_id=publish_id, status=PublishStatus.FAILED,
                message=f"Build stopped; publication is {e.status}", action="process",
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
            try:
                self._ext_state.update_status(
                    publish_id=publish_id,
                    target_status=PublishStatus.FAILED,
                    source_status=PublishStatus.BUILDING,
                    ext=ext,
                    expected_ext=expected_ext,
                )
            except PublishNotFoundError:
                current = self._ext_state.get_record(publish_id)
                if current.status == PublishStatus.BUILDING.value:
                    raise
                return PublishFlowResult(
                    publish_id=publish_id, status=PublishStatus.FAILED,
                    message=f"Build stopped; publication is {current.status}", action="process",
                )

            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.FAILED,
                message=f"Build failed: {error_message}",
                action="process",
            )
