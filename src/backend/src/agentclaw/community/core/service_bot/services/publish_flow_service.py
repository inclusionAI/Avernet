"""Bot publish flow processing service.

Advances the different stages of the publish flow based on the publish record status.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from injector import inject

from agentclaw.community.core.service_bot.repository.models import (
    PublishStatus,
    BotPublishRecord,
    PublishOperationState,
)
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
from agentclaw.community.core.service_bot.services.publish_flow.device_binding_mixin import (
    DeviceBindingMixin,
)
from agentclaw.community.core.service_bot.services.publish_flow.publish_ext_mixin import (
    PublishExtMixin,
)
from agentclaw.community.core.service_bot.services.publish_flow.eval_publish_mixin import (
    EvalPublishMixin,
)
from agentclaw.community.core.service_bot.services.publish_flow.upgrade_resolution_mixin import (
    UpgradeResolutionMixin,
)
from agentclaw.community.core.service_bot.services.publish_flow.release_stage import (
    ONLINE_SPEC,
    VERIFY_SPEC,
    ReleaseStageRunner,
)
from agentclaw.community.core.service_bot.services.publish_flow.operation_runner import (
    PublishOperationRunner,
)
from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
    enqueue_destroy,
    enqueue_online_release,
    enqueue_progress_poll,
    enqueue_verify_flow,
)
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.log import get_logger


if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.core.service_bot.repository.publish_operation_protocol import (
        PublishOperationRepositoryProtocol,
    )
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

# Describe-only messages for the non-user-driven statuses that ``process()`` just
# reports back (no side effects). DRAFT and VALIDATING are handled separately —
# they are the only user-driven advance points. FAILED is special-cased (it needs
# the ext error message) and any status absent here is an invalid/unknown state.
_DESCRIBE_STATUS_MESSAGES = {
    PublishStatus.BUILT: "Build complete, publish in progress, please check progress later",
    PublishStatus.BUILDING: "Build in progress, please wait",
    PublishStatus.VALIDATE_PUB: "Verify environment publish in progress, please wait",
    PublishStatus.ONLINE_PUB: "Online publish in progress, please wait",
    PublishStatus.SUCCESS: "Publish complete",
}

# Statuses ``process()`` never advances *through*: DRAFT/VALIDATING are its advance
# points (intercepted before describe-dispatch); UPGRADED/RELEASED are terminal.
# Both describe paths (_describe_publish_status, describe_publish) fall back through
# this table so a superseded/offline record reports a friendly message, not an error.
_SYNC_ONLY_STATUS_MESSAGES = {
    PublishStatus.DRAFT: "Draft, publish not started",
    PublishStatus.VALIDATING: "Verify environment ready, awaiting online publish confirmation",
    PublishStatus.UPGRADED: "Superseded by a newer published version",
    PublishStatus.RELEASED: "Taken offline",
}


class PublishFlowService(
    ProgressSyncMixin,
    RestartMixin,
    ScaleMixin,
    StageStatusMixin,
    RollbackOpsMixin,
    BaasPublishOpsMixin,
    DeviceBindingMixin,
    PublishExtMixin,
    EvalPublishMixin,
    UpgradeResolutionMixin,
):
    """Bot publish flow processing service.

    Responsibilities:
    - Determine the current stage based on the publish record status
    - Coordinate BotBuildService and BotPublishService to complete the publish flow
    - Manage status transitions

    Status transitions:
    - DRAFT -> BUILDING -> BUILT (build stage)
    - BUILT -> VALIDATE_PUB -> VALIDATING (verify environment publish stage)
    - VALIDATING -> ONLINE_PUB -> SUCCESS (online publish stage)
    - Any status -> FAILED (failure)
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
        task_queue_service: TaskQueueService,
        publish_operation_repo: "PublishOperationRepositoryProtocol",
    ):
        """Initialize the flow processing service.

        Args:
            bot_publish_service: Publish record management service
            bot_build_service: Bot build service
            baas_service: BaaS layer API service
            bot_service: Bot management service (used to fetch bot info during the build/publish stages)
            producer_router: Router that selects the build artifact producer by ``device_provider``.
                Assembled by the DI root (`service_bot_module`) — currently only includes ARCA (arca/baas →
                existing ``build()``, behaviorally equivalent); external/teclaw producers are registered once the
                ConfigComposer collector's DI lands (Group C/D follow-up).
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

        # Crash-safe operation ledger + step runner (#197): every BaaS mutation
        # in the release/restart/offline/rollback/eval/approval paths goes through
        # this so a crash-resume adopts an in-doubt workflow instead of re-issuing.
        self._publish_operation_repo = publish_operation_repo
        self._operation_runner = PublishOperationRunner(
            ledger=publish_operation_repo,
            baas_service=baas_service,
        )

        # Stage-parameterized release runner: one first-release + one upgrade
        # implementation for both verify and online (was four near-duplicate
        # methods). The runners take their real dependencies explicitly; the
        # facade satisfies ReleaseRecordOps (the four public release-record ops).
        self._release_runner = ReleaseStageRunner(
            ext_state=self._ext_state,
            build_service=bot_build_service,
            baas_service=baas_service,
            provider_behaviors=self._provider_behaviors,
            ops=self,
            operation_runner=self._operation_runner,
        )
        self._build_stage_runner = BuildStageRunner(
            ext_state=self._ext_state,
            bot_service=bot_service,
            baas_service=baas_service,
            producer_router=producer_router,
            provider_behaviors=self._provider_behaviors,
        )

        # Durable task queue: backend-driven advances (build+verify release, online
        # release) are enqueued as persisted, crash-safe tasks instead of
        # fire-and-forget asyncio tasks. See publish_flow/tasks.py.
        self._task_queue_service = task_queue_service

    def refresh_publish_handle(self, binding_id, publish_id) -> None:
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
                "[PublishFlowService.refresh_publish_handle] failed for "
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
        """Advance the publish flow.

        Only two statuses are user-driven advance points; both move the status
        forward synchronously under the optimistic lock (so a concurrent
        double-submit loses the CAS) and then enqueue the durable task that owns
        the remainder:
        - DRAFT: advance DRAFT → BUILDING, enqueue verify_flow (build + verify release)
        - VALIDATING: advance VALIDATING → ONLINE_PUB, enqueue online_release

        Every other status is a side-effect-free status report (the task chain
        drives it forward on its own): BUILDING / BUILT / VALIDATE_PUB / ONLINE_PUB
        / SUCCESS return an "in progress" (or "complete") message, and FAILED
        returns the recorded error.

        Args:
            publish_id: Publish record ID
            operator: Operator ID

        Returns:
            PublishFlowResult: Publish result
        """
        logger.info(
            f"[PublishFlowService.process] called: publish_id={publish_id}, operator={operator}"
        )

        # Step 1: Query the publish record
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)

        logger.info(f"[PublishFlowService] Current status: {current_status}")

        # Step 2: The two user-driven advance points move the status forward
        # synchronously under the optimistic lock *before* enqueuing the durable
        # task, so a concurrent double-submit loses the CAS (only the winner
        # advances and enqueues — no double build / double online release). The
        # infra task then owns only the remainder: the verify_flow task drives
        # BUILDING -> BUILT -> VALIDATE_PUB; the online_release task drives the
        # release within ONLINE_PUB and the poll drives ONLINE_PUB -> SUCCESS.
        # Every other status is a side-effect-free status query.
        if current_status == PublishStatus.DRAFT:
            if not self._advance_status(
                publish_id, PublishStatus.BUILDING, PublishStatus.DRAFT
            ):
                # Lost the race to a concurrent submit → report current progress.
                return self._describe_current_status(publish_id)
            enqueue_verify_flow(
                self._task_queue_service, publish_id=publish_id, operator=operator
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.BUILDING,
                message="Build started, please check progress later",
                action="process",
            )

        if current_status == PublishStatus.VALIDATING:
            # Online gate: only the user's /process opens ONLINE_PUB.
            if not self._advance_status(
                publish_id, PublishStatus.ONLINE_PUB, PublishStatus.VALIDATING
            ):
                return self._describe_current_status(publish_id)
            enqueue_online_release(
                self._task_queue_service, publish_id=publish_id, operator=operator
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.ONLINE_PUB,
                message="Online publish submitted, please check progress later",
                action="process",
            )

        # Not a user advance point → describe-only (the task chain drives these).
        return self._describe_publish_status(publish_record, current_status)

    def _advance_status(
        self,
        publish_id: int,
        target_status: PublishStatus,
        source_status: PublishStatus,
    ) -> bool:
        """Atomically advance a publish record's status under the optimistic lock.

        Status-only (no ext write). Returns ``True`` if this call won the
        transition (the record was still at ``source_status``); ``False`` if it
        lost — a concurrent submit already moved it (the double-submit guard)."""
        return self._ext_state.advance_status(publish_id, target_status, source_status)

    def _describe_current_status(self, publish_id: int) -> PublishFlowResult:
        """Re-read the record and describe its (now concurrently-advanced) status."""
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")
        return self._describe_publish_status(
            publish_record, PublishStatus(publish_record.status)
        )

    def describe_publish(self, publish_id: int) -> PublishFlowResult:
        """Report the publish record's current status. Read-only.

        Backs the user-invokable ``POST /publish/{id}/sync`` endpoint. Since the
        durable task pipeline owns all status advancement (the poll task drives
        ``advance_publish_progress``), an external status query must not mutate —
        it just reads the record and describes it, covering every status.
        """
        publish_record = self.get_publish_record(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)
        if current_status == PublishStatus.FAILED:
            error_message = (publish_record.ext or {}).get("error_message")
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"Publish failed: {error_message or 'Unknown error'}",
                action="sync",
            )

        message = _DESCRIBE_STATUS_MESSAGES.get(
            current_status
        ) or _SYNC_ONLY_STATUS_MESSAGES.get(current_status)
        if message is None:
            raise PublishStatusInvalidError(f"Unknown publish status: {current_status}")
        return PublishFlowResult(
            publish_id=publish_id,
            status=current_status,
            message=message,
            action="sync",
        )

    @staticmethod
    def _describe_publish_status(
        publish_record: BotPublishRecord,
        current_status: PublishStatus,
    ) -> PublishFlowResult:
        """Report a publish record's current status without side effects.

        Used by ``process()`` for every status that is not a user-driven advance
        point (the durable task chain drives those forward on its own)."""
        if current_status == PublishStatus.FAILED:
            error_message = (publish_record.ext or {}).get("error_message")
            return PublishFlowResult(
                publish_id=publish_record.id,
                status=current_status,
                message=f"Publish failed: {error_message or 'Unknown error'}",
                action="process",
            )

        # Fall back through _SYNC_ONLY_STATUS_MESSAGES: a record can reach a terminal
        # state (UPGRADED/RELEASED) concurrently — describe it, don't raise.
        message = _DESCRIBE_STATUS_MESSAGES.get(
            current_status
        ) or _SYNC_ONLY_STATUS_MESSAGES.get(current_status)
        if message is None:
            raise PublishStatusInvalidError(f"Unknown publish status: {current_status}")
        return PublishFlowResult(
            publish_id=publish_record.id,
            status=current_status,
            message=message,
            action="process",
        )

    def _provider_behavior(self, bot: dict):
        """The :class:`ProviderBehavior` for ``bot``'s container, resolved via the
        same ``resolve_container_provider`` mapping used for producer selection."""
        return self._provider_behaviors.resolve(
            self._baas_service.resolve_container_provider(bot)
        )

    # ── phase entry points for the durable task handlers ─────────────────────
    # Public: the task handlers (publish_flow/tasks.py) are an external consumer
    # (queue-adapter layer, own lifecycle) — these three phase methods ARE the
    # facade's contract for them. The finer-grained helpers each phase dispatches
    # to (_execute_verify_upgrade / _execute_first_release / …) stay private:
    # nothing outside this class calls them.
    async def execute_build_phase(
        self,
        publish_record: BotPublishRecord,
        operator: str,
    ) -> PublishFlowResult:
        """Run the build stage (BUILDING → BUILT). Delegates to BuildStageRunner."""
        return await self._build_stage_runner.build(publish_record, operator)

    async def execute_verify_release_phase(
        self,
        publish_record: BotPublishRecord,
        operator: str,
    ) -> PublishFlowResult:
        """Run the verify environment publish stage.

        Chooses upgrade vs. first release based on whether the verify environment already has a Bot:
        - Existing Bot (ext.binding.verify present and the binding record is valid): call _execute_verify_upgrade
        - No existing Bot: call _execute_verify_first_release

        Args:
            publish_record: Publish record
            operator: Operator

        Returns:
            PublishFlowResult: Publish result
        """
        publish_id = publish_record.id
        bot_id = publish_record.source_bot_id
        owner_id = self._get_owner_id(publish_record)

        # Fetch the build artifact from the ext field. ARCA = migration_path (mounted); teclaw = frozen
        # config_artifact (non-mounted delivery). Neither present → not yet built.
        migration_path = None
        config_artifact = None
        if publish_record.ext:
            migration_path = publish_record.ext.get("migration_path")
            config_artifact = publish_record.ext.get("config_artifact")

        if not migration_path and not config_artifact:
            error_msg = "Build artifact path does not exist, please run the build first"
            logger.error(f"[PublishFlowService.execute_verify_release_phase] publish_id={publish_id}, {error_msg}")
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
                f"[PublishFlowService.execute_verify_release_phase] "
                f"Starting release to verify environment: publish_id={publish_id}, "
                f"bot_id={bot_id}, operator={operator}, owner_id={owner_id}"
            )

            # Fetch Bot info
            bot = self._bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
            if not bot:
                raise PublishFlowServiceError(f"Bot does not exist: {bot_id}")

            ext = self._get_latest_ext(publish_id)

            # ========== Determine whether the verify environment already has a Bot ==========
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
            logger.error(f"[PublishFlowService.execute_verify_release_phase] Release failed: {e}")

            # Publish failed; update the status and error info
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
                message=f"Publish to verify environment failed: {str(e)}",
                action="process",
            )

    async def _execute_verify_first_release(
            self,
            publish_record: BotPublishRecord,
            operator: str,
            migration_path: str,
            bot: dict,
    ) -> PublishFlowResult:
        """Run the verify environment first release (create a new Bot). Delegates to the unified ReleaseStageRunner."""
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
        """Run the verify environment upgrade release (reuse an existing Bot). Delegates to the unified ReleaseStageRunner."""
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

    async def execute_release_phase(
        self,
        publish_record: BotPublishRecord,
        operator: str,
    ) -> PublishFlowResult:
        """Run the publish stage.

        Determines first release vs. upgrade release based on last_pub_id:
        - First release: call BotBuildService.release_async() to create a new Bot
        - Upgrade release: call BotBuildService.upgrade_async() to update the existing Bot

        Args:
            publish_record: Publish record
            operator: Operator

        Returns:
            PublishFlowResult: Publish result
        """
        publish_id = publish_record.id
        bot_id = publish_record.source_bot_id
        owner_id = self._get_owner_id(publish_record)

        # Fetch the build artifact from the ext field. ARCA = migration_path (mounted); teclaw = frozen
        # config_artifact (non-mounted delivery). Neither present → not yet built.
        migration_path = None
        config_artifact = None
        if publish_record.ext:
            migration_path = publish_record.ext.get("migration_path")
            config_artifact = publish_record.ext.get("config_artifact")

        if not migration_path and not config_artifact:
            error_msg = "Build artifact path does not exist, please run the build first"
            logger.error(f"[PublishFlowService]{publish_id}, publish_record={publish_record},  {error_msg}")
            ext = self._get_latest_ext(publish_id)
            self._clear_retry_flag(ext)
            ext["error_message"] = error_msg
            # The online release runs within ONLINE_PUB (process owns the
            # VALIDATING -> ONLINE_PUB advance), so failures roll back to ONLINE_PUB.
            ext["source_status"] = PublishStatus.ONLINE_PUB.value
            self._update_publish_status(
                publish_id=publish_id,
                target_status=PublishStatus.FAILED,
                source_status=PublishStatus.ONLINE_PUB,
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

            # Fetch Bot info
            bot = self._bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
            if not bot:
                raise PublishFlowServiceError(f"Bot does not exist: {bot_id}")

            # ========== Core logic: determine whether this is an upgrade scenario ==========
            if self._should_upgrade_online(publish_record):
                # Upgrade release: reuse the existing Bot
                return await self._execute_upgrade_release(
                    publish_record=publish_record,
                    operator=operator,
                    migration_path=migration_path,
                    bot=bot,
                )
            else:
                # First release: create a new Bot
                return await self._execute_first_release(
                    publish_record=publish_record,
                    operator=operator,
                    migration_path=migration_path,
                    bot=bot,
                )

        except Exception as e:
            logger.error(f"[PublishFlowService] Release failed: {e}")

            # Publish failed; update the status and error info
            ext = self._get_latest_ext(publish_id)
            self._clear_retry_flag(ext)
            ext["error_message"] = str(e)
            # Roll back to ONLINE_PUB (the state the release runs within).
            ext["source_status"] = PublishStatus.ONLINE_PUB.value
            self._update_publish_status(
                publish_id=publish_id,
                target_status=PublishStatus.FAILED,
                source_status=PublishStatus.ONLINE_PUB,
                ext=ext,
            )

            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.FAILED,
                message=f"Publish failed: {str(e)}",
                action="process",
            )

    async def _execute_first_release(
            self,
            publish_record: BotPublishRecord,
            operator: str,
            migration_path: str,
            bot: dict,
    ) -> PublishFlowResult:
        """Run the online first release (create a new Bot). Delegates to the unified ReleaseStageRunner."""
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
        """Run the online upgrade release (reuse the existing BaaS Bot).

        First resolves the online binding / bot_uuid (online-specific) from last_pub_id, then delegates to the
        unified ReleaseStageRunner.upgrade_release.
        """
        publish_id = publish_record.id
        last_pub_id = publish_record.last_pub_id

        # Resolve the previous publish record's online binding → bot_uuid (reuse the existing Bot, no new record).
        last_publish = self._publish_service.get_publish_by_id(last_pub_id)
        if not last_publish:
            raise PublishFlowServiceError(f"Previous publish record does not exist: last_pub_id={last_pub_id}")

        last_binding_info = (last_publish.ext or {}).get("binding", {})
        online_binding_id = last_binding_info.get(PublishStage.ONLINE.value)
        if not online_binding_id:
            raise PublishFlowServiceError(
                f"Previous publish record has no online environment binding info: last_pub_id={last_pub_id}"
            )

        binding = self._publish_service.get_device_binding_by_id(online_binding_id)
        if not binding:
            raise PublishFlowServiceError(f"Device binding record does not exist: binding_id={online_binding_id}")

        bot_uuid = binding.device_id
        if not bot_uuid:
            raise PublishFlowServiceError(
                f"Device binding record has no device_id: binding_id={online_binding_id}"
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
        """Retry a failed publish flow.

        Based on the pre-failure status (ext.source_status), roll back to the corresponding status and
        re-advance the flow:
        - building → roll back to BUILDING, rebuild + verify publish
        - built → roll back to BUILT, re-run verify publish
        - validate_pub → roll back to VALIDATE_PUB, call BaaS restart to retry
        - online_pub → roll back to ONLINE_PUB; if the online release was already
          recorded (BaaS-wait failure) call BaaS restart, otherwise re-run the
          online release work via the online_release task

        Args:
            publish_id: Publish record ID
            operator: Operator

        Returns:
            PublishFlowResult: Retry result

        Raises:
            PublishNotFoundError: Publish record does not exist
            PublishFlowServiceError: Status does not support retry or rollback failed
        """
        logger.info(
            f"[PublishFlowService.retry] called: publish_id={publish_id}, operator={operator}"
        )

        # Step 1: Query the publish record
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)

        # Step 2: Verify the status is FAILED
        if current_status != PublishStatus.FAILED:
            raise PublishFlowServiceError(
                f"Current status {current_status} does not support retry; only FAILED status can be retried"
            )

        # Step 3: Get the pre-failure status from ext
        ext = self._get_latest_ext(publish_id)
        source_status = ext.get("source_status")
        if not source_status:
            raise PublishFlowServiceError(
                f"Publish record is missing pre-failure status info (source_status); cannot retry: publish_id={publish_id}"
            )

        # Step 4: Determine the rollback target status and retry action based on
        # source_status. A build failure rolls back to BUILDING (not DRAFT): the
        # user-driven DRAFT -> BUILDING advance already happened, and the verify_flow
        # task rebuilds from BUILDING.
        retry_map = {
            PublishStatus.BUILDING.value: PublishStatus.BUILDING,
            PublishStatus.BUILT.value: PublishStatus.BUILT,
            PublishStatus.VALIDATE_PUB.value: PublishStatus.VALIDATE_PUB,
            PublishStatus.VALIDATING.value: PublishStatus.VALIDATING,
            PublishStatus.ONLINE_PUB.value: PublishStatus.ONLINE_PUB,
            PublishStatus.SUCCESS.value: PublishStatus.SUCCESS,
        }

        rollback_status = retry_map.get(source_status)
        if not rollback_status:
            raise PublishFlowServiceError(
                f"Unsupported retry scenario: source_status={source_status}, publish_id={publish_id}"
            )

        # Step 5: Roll back the status (FAILED -> rollback_status) and set the retry flag
        ext["retry"] = True
        try:
            self._update_publish_status(
                publish_id=publish_id,
                target_status=rollback_status,
                source_status=PublishStatus.FAILED,
                ext=ext,
            )
        except Exception as e:
            raise PublishFlowServiceError(
                f"Status rollback failed: {rollback_status.value}, error={e}"
            )

        logger.info(
            f"[PublishFlowService.retry] Status rolled back: "
            f"publish_id={publish_id}, FAILED -> {rollback_status.value}"
        )

        # Step 6: Execute the retry action. Directly enqueue the corresponding task
        # (no longer via process(), because /process is already read-only for BUILT;
        # a BUILT retry must re-drive verify_flow).
        #
        # A BaaS-level restart applies when the release already reached the BaaS
        # layer and *it* failed: the *_PUB wait states, SUCCESS, and an ONLINE_PUB
        # whose online release was already recorded (poll failure). An ONLINE_PUB
        # whose release was never recorded means the release *work* itself failed,
        # so re-run it via the online_release task instead.
        restart = rollback_status in (
            PublishStatus.VALIDATE_PUB,
            PublishStatus.SUCCESS,
        ) or (
            rollback_status == PublishStatus.ONLINE_PUB
            and self.is_online_release_recorded(publish_id)
        )
        if restart:
            # BaaS publish failed; call restart_bot to retry
            restart_result = self.restart_bot(
                publish_id=publish_id,
                operator=operator,
            )
            success = restart_result.get("success", False)
            if success:
                # The BaaS-restart branch parks the record in its *_PUB wait state
                # without passing through verify_flow/online_release, so pre-#105 it
                # advanced only via user /sync polling (retry redirect) or an explicit
                # /restart_status poll. Enqueue the durable poll so the retried
                # restart self-drives: the poll's retry-flag redirect routes it
                # through sync_restart_progress and leaves the *_PUB state.
                enqueue_progress_poll(self._task_queue_service, publish_id=publish_id)
            else:
                self._mutate_and_update_ext(
                    publish_id=publish_id,
                    mutator=self._clear_retry_flag,
                )
            return PublishFlowResult(
                publish_id=publish_id,
                status=rollback_status,
                action="restart",
                message="Retry submitted (BaaS restart)" if success else f"Retry failed: {restart_result.get('message', 'Unknown error')}",
            )
        elif rollback_status in (PublishStatus.VALIDATING, PublishStatus.ONLINE_PUB):
            # Online release retry: re-open ONLINE_PUB (idempotent if already there)
            # and re-enqueue the online_release task, which re-runs the release work.
            self._advance_status(
                publish_id, PublishStatus.ONLINE_PUB, PublishStatus.VALIDATING
            )
            enqueue_online_release(
                self._task_queue_service, publish_id=publish_id, operator=operator
            )
        else:
            # BUILDING / BUILT: re-enqueue the verify_flow task (the build sub-step
            # is skipped when already BUILT).
            if rollback_status == PublishStatus.BUILDING:
                # A rebuild changes the artifact, so any release op from the failed
                # attempt is superseded — abandon it (#197 abandonment) so the fresh
                # attempt opens new ledger ops rather than resuming/adopting a stale
                # workflow built from the old artifact.
                self._abandon_inflight_operations(
                    publish_id, reason="retry rebuild — superseded"
                )
            enqueue_verify_flow(
                self._task_queue_service, publish_id=publish_id, operator=operator
            )
        return PublishFlowResult(
            publish_id=publish_id,
            status=rollback_status,
            action="process",
            message="Retry submitted, please check progress later",
        )

    def enqueue_offline_destroy(self, publish_id: int, stage, operator: str) -> None:
        """Enqueue the durable destroy task for an offline (#197) — replaces the
        former fire-and-forget background destroy. ``stage`` is a PublishStage."""
        enqueue_destroy(
            self._task_queue_service,
            publish_id=publish_id,
            stage=stage.value if hasattr(stage, "value") else str(stage),
            operator=operator,
        )

    def _abandon_inflight_operations(self, publish_id: int, reason: str) -> None:
        """Abandon every non-terminal ledger op for a publish record (#197).

        Used when the record is restarted from an earlier phase (rebuild) or
        superseded by a new version — the in-flight ops are no longer the current
        intent, so a fresh attempt must open new ops rather than resume these."""
        terminal = {s.value for s in PublishOperationState.terminal()}
        for op in self._publish_operation_repo.list_by_publish(publish_id):
            if op.state not in terminal:
                self._publish_operation_repo.abandon(op.id, reason)

    def _get_owner_id(self, publish_record: BotPublishRecord) -> str:
        return self._ext_state.owner_id(publish_record)

    @staticmethod
    def _clear_retry_flag(ext: dict) -> None:
        PublishExtState.clear_retry_flag(ext)

    def _get_latest_ext(self, publish_id: int) -> dict:
        return self._ext_state.get_latest_ext(publish_id)

    def _mutate_and_update_ext(
        self,
        publish_id: int,
        mutator: Callable[[dict], None],
    ) -> dict:
        return self._ext_state.mutate_and_update_ext(publish_id, mutator)

    def _update_publish_status(
        self,
        publish_id: int,
        target_status: PublishStatus,
        source_status: PublishStatus,
        ext: dict,
    ) -> None:
        self._ext_state.update_status(
            publish_id=publish_id,
            target_status=target_status,
            source_status=source_status,
            ext=ext,
        )

    # ── public accessors for the durable task handlers ───────────────────────
    # The task handlers (publish_flow/tasks.py) orchestrate this facade; expose the
    # publish-record reads/writes they need as public methods so they don't reach
    # into the private ``_publish_service`` collaborator.
    def get_publish_record(self, publish_id: int) -> BotPublishRecord | None:
        """Fetch a publish record by id (``None`` if absent)."""
        return self._publish_service.get_publish_by_id(publish_id)

    def is_online_release_recorded(self, publish_id: int) -> bool:
        """True once this record's online release is fully recorded — i.e. the
        binding + ``ext.publish.online`` were written, not merely the BaaS
        workflow id.

        This is the crash-resume guard for the online leg, which runs *within*
        ONLINE_PUB with no status transition to guard it, so the threshold must
        be the *completed* release, not just the created workflow. Both consumers
        need this threshold:

        * the online_release task gate (``tasks.py``) skips re-running the release
          only when it is fully done; at ``ID_RECORDED``-but-not-complete a re-run
          MUST re-enter (the runner then resumes: reuses the in-flight op + binding
          and finishes the ext write) — gating on the mere workflow id would strand
          the record with an orphaned bot (binding/ext never written).
        * ``retry`` chooses BaaS-restart only for a completed release (a BaaS-side
          failure); a partial release re-runs the release work instead.

        Ledger-driven (#197): the latest online-stage op is ``COMPLETED``. The
        ``ext.publish.online`` marker (written in the release's ext step, one step
        before ``complete_operation``) is the fallback — it covers both the tiny
        record-ext→complete window and records that predate the ledger."""
        for kind in ("online_first_release", "online_upgrade"):
            op = self._publish_operation_repo.get_latest_by_kind(
                publish_id, kind, PublishStage.ONLINE.value
            )
            if op is not None and op.state == PublishOperationState.COMPLETED.value:
                return True
        record = self.get_publish_record(publish_id)
        ext = (record.ext or {}) if record else {}
        return bool(ext.get("publish", {}).get(PublishStage.ONLINE.value))

