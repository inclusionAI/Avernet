"""Publish workflow orchestration service.

Manages Bot lifecycle changes through multi-stage deployment pipelines
with approval gates and rolling updates. Per D-01, D-01a, D-07.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from secbaas.community.core.repository.bot import BotRecord
    from secbaas.community.core.repository.publish import PublishRecord

from secbaas.community.api.bot_manage import BotManageService, BotStatus
from secbaas.community.api.bot_runtime import BotNotFoundError
from secbaas.community.api.device_manage import DeviceService
from secbaas.community.api.publish_manage import (
    DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS,
    BatchDeviceProgress,
    BatchResult,
    BatchStatus,
    BotPublishSummary,
    DeviceCallbackRequest,
    DeviceOperationResult,
    DrainResult,
    ProgressSummary,
    ProgressTimeline,
    PublishBatchConfig,
    PublishConfig,
    PublishConflictError,
    PublishEventType,
    PublishNotFoundError,
    PublishProgressResponse,
    PublishRecordResult,
    PublishResponse,
    PublishService,
    PublishStage,
    PublishStatus,
    PublishType,
    RestartScope,
    StageConfig,
    StageProgress,
    serialize_hook_result,
)
from secbaas.community.api.template_manage import DeviceTemplateManageService
from secbaas.community.core.repository.bot import (
    BotRepository,
)
from secbaas.community.core.repository.bot_device_rel import (
    BotDeviceRelRepository,
)
from secbaas.community.core.repository.bot_session import BotSessionRepository
from secbaas.community.core.repository.device import (
    DeviceRepository,
)
from secbaas.community.core.repository.publish import (
    PublishRepository,
)
from secbaas.community.core.repository.publish_batch import (
    PublishBatchRecord,
    PublishBatchRepository,
)
from secbaas.community.core.repository.publish_record import (
    PublishRecordRecord,
    PublishRecordRepository,
)
from secbaas.community.core.service.paas import is_paas_mock_mode
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

logger = get_logger("core-service")


def _extra_config_to_publish_config(
    extra_config: dict[str, Any] | None,
) -> PublishConfig | None:
    """Convert extra_config dict to PublishConfig model."""
    if extra_config:
        return PublishConfig.model_validate(extra_config)
    return None


@dataclass
class BatchConfig:
    """Internal batch configuration for stage generation."""

    batch_index: int
    stage: str
    batch_capacity: int
    cooldown_seconds: int
    device_count: int


class DefaultPublishService(PublishService):
    """Bot publish workflow orchestration service.

    State Machine Overview:
        Publish documents progress through states: PENDING → ACTIVE → APPROVING → SUCCESS
        Terminal states: REJECTED, FAILED, SUCCESS, REVOKED
        See PublishStatus enum for full transition table.

    Pipeline Stages by PublishType (Per D-01, D-01a):
        - CREATE/UPDATE: PREPUB → GRAY → PROD_FIRST_BATCH → PROD_OTHER_BATCH → SUCCESS
        - RESTART: PROD_FIRST_BATCH → PROD_OTHER_BATCH → SUCCESS
        - SCALE_UP/SCALE_DOWN/DESTROY: Direct execution (single batch, no stage gates)

    Key Features:
        - Stage gates with approval (pause_for_approval)
        - Auto-compact for small device counts
        - Batch execution with cooldown periods
        - Concurrent publish prevention per bot
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        device_repo: DeviceRepository,
        rel_repo: BotDeviceRelRepository,
        session_repo: BotSessionRepository,
        publish_repo: PublishRepository,
        batch_repo: PublishBatchRepository,
        publish_record_repo: PublishRecordRepository,
        template_service: DeviceTemplateManageService,
        bot_service: BotManageService,
        device_service: DeviceService,
    ) -> None:
        """Initialize with injected dependencies."""
        self._bot_repo = bot_repo
        self._device_repo = device_repo
        self._rel_repo = rel_repo
        self._session_repo = session_repo
        self._publish_repo = publish_repo
        self._publish_batch_repo = batch_repo
        self._publish_record_repo = publish_record_repo
        self._template_service = template_service
        self._bot_service = bot_service
        self._device_service = device_service

    # ====================================================================
    # UTILITY METHODS
    # ====================================================================
    def _cleanup_pending_clone_on_update_failure(
        self,
        tenant: str,
        publish_id: int,
        operator: str,
    ) -> None:
        """Clean up orphaned PENDING bot clone when an UPDATE publish fails.

        When an UPDATE publish transitions to FAILED, the PENDING clone created
        during create_publish() must be cleaned up to free the unique key slot
        (tenant, bot_uuid, env, status=PENDING, is_deleted=0). Without this
        cleanup, subsequent UPDATE publishes for the same bot_uuid will hit a
        duplicate key violation.

        Also soft-deletes any bot_device_rel rows on the orphan clone (should
        always be zero since device transfer happens only at SUCCESS, but the
        cleanup is harmless and future-proof).
        """
        from secbaas.community.api.bot_manage import BotStatus

        env = get_current_env()
        publish_repo = self._publish_repo
        bot_repo = self._bot_repo
        rel_repo = self._rel_repo

        publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
        if publish_record is None:
            return

        target_bot_id = (publish_record.extra_config or {}).get("target_bot_id")
        if target_bot_id is None:
            return

        target_bot = bot_repo.get_by_id(target_bot_id, tenant=tenant, env=env)
        if target_bot is None:
            return
        if target_bot.status != BotStatus.PENDING.value:
            return

        try:
            rel_repo.soft_delete_by_bot_id(
                bot_id=target_bot_id,
                tenant=tenant,
                env=env,
                modifier=operator,
            )
        except Exception:
            logger.warning(
                f"[cleanup_pending] Failed to soft-delete device relationships "
                f"for orphan PENDING bot {target_bot_id} (may have none)",
                exc_info=True,
            )

        bot_repo.soft_delete(
            bot_id=target_bot_id,
            tenant=tenant,
            env=env,
            modifier=operator,
        )
        logger.info(
            f"[cleanup_pending] Soft-deleted orphan PENDING bot {target_bot_id} "
            f"after UPDATE publish {publish_id} → FAILED"
        )

    # ====================================================================
    # STATE MACHINE AND APPROVAL METHODS
    # ====================================================================

    # Per D-02: Valid state transitions (current_status, action) -> new_status
    #
    # Transition Categories:
    # ┌─────────────────────┬────────────────────────────────────────────────────┐
    # │ Category            │ Description                                        │
    # ├─────────────────────┼────────────────────────────────────────────────────┤
    # │ Initial Approval    │ PENDING → ACTIVE (approve) or REJECTED (reject)    │
    # │ Stage Completion    │ ACTIVE → APPROVING (stage complete, gate pending)  │
    # │                     │ ACTIVE → SUCCESS (all stages complete)             │
    # │                     │ ACTIVE → FAILED (execution error)                  │
    # │ Gate Approval       │ APPROVING → ACTIVE (approve, resume)               │
    # │                     │ APPROVING → REJECTED (reject)                      │
    # │                     │ APPROVING → REVOKED (revoke)                       │
    # └─────────────────────┴────────────────────────────────────────────────────┘
    #
    # Methods that trigger transitions:
    # - approve_publish(): Triggers "approve" action (PENDING/APPROVING → ACTIVE)
    # - reject_publish(): Triggers "reject" action (PENDING/APPROVING → REJECTED)
    # - revoke_publish(): Triggers "revoke" action (APPROVING → REVOKED)
    # - _transition_on_stage_complete(): Triggers "stage_complete" or "all_complete"
    # - _mark_failed(): Triggers "fail" action (ACTIVE → FAILED)
    TRANSITIONS: dict[tuple[str, str], str] = {
        # Initial approval
        ("PENDING", "approve"): "ACTIVE",
        ("PENDING", "reject"): "REJECTED",
        # Stage execution completion
        ("ACTIVE", "stage_complete"): "APPROVING",
        ("ACTIVE", "all_complete"): "SUCCESS",
        ("ACTIVE", "fail"): "FAILED",
        # Stage gate approval
        ("APPROVING", "approve"): "ACTIVE",
        ("APPROVING", "reject"): "REJECTED",
        ("APPROVING", "revoke"): "REVOKED",
    }

    def _can_transition(self, current_status: str, action: str) -> bool:
        """Check if state transition is valid."""
        return (current_status, action) in self.TRANSITIONS

    def _get_next_status(self, current_status: str, action: str) -> str | None:
        """Get next status after transition."""
        return self.TRANSITIONS.get((current_status, action))

    async def create_publish(
        self,
        tenant: str,
        bot_id: int,
        publish_type: PublishType,
        operator: str,
        request_id: str,
        config: PublishConfig | None = None,
    ) -> PublishResponse:
        """
        Create new publish request with stage configuration.

        Flow:
        1. Validate bot exists and tenant matches (tenant isolation per D-01 pattern)
        2. Validate no concurrent active publish (SVC-PUB-15)
        3. Generate batch configuration for publish type (D-01a)
        4. Create baas_publish record with status=PENDING (awaiting approval)
        5. Create baas_publish_batch records for each stage
        6. Return publish response

        Args:
            tenant: Tenant name for isolation
            bot_id: Bot to publish
            publish_type: Type of publish (CREATE, UPDATE, RESTART, SCALE_UP, SCALE_DOWN)
            operator: User creating the publish
            request_id: Request ID for correlation (client-provided, required)
            config: Publish configuration (stages, bot_name, replica_desired, etc.)

        Returns:
            PublishResponse for created publish

        Raises:
            ValueError: If bot not found, tenant mismatch, or concurrent active publish
        """
        env = get_current_env()
        logger.info(
            f"[create_publish] tenant={tenant}, env={env}, bot={bot_id}, type={publish_type.value}, request_id={request_id}"
        )

        # Step 1: Validate bot exists and tenant matches
        bot = await self._bot_service.get_bot(
            tenant=tenant, bot_id=bot_id, include_status=False
        )
        if bot is None:
            logger.warning(
                f"Bot not found or tenant mismatch: bot={bot_id}, tenant={tenant}"
            )
            raise BotNotFoundError(str(bot_id))

        # Step 2: Check for concurrent active publish (SVC-PUB-15)
        publish_repo = self._publish_repo
        active_publish = publish_repo.get_active_by_bot_id(
            bot_id=bot_id, tenant=tenant, env=env
        )
        if active_publish is not None:
            # Check if active publish is an orphan (has no batch records),
            # which indicates the previous create_publish call was interrupted
            # after inserting baas_publish but before inserting baas_publish_batch.
            # Orphan publishes block new publishes forever since they never
            # transition out of PENDING. Auto-clean them so the new publish
            # can proceed.
            batch_repo = self._publish_batch_repo
            existing_batches = batch_repo.list_by_publish_id(
                active_publish.id, tenant, env
            )
            if not existing_batches:
                logger.warning(
                    f"Orphan publish detected: id={active_publish.id}, "
                    f"type={active_publish.publish_type}, status={active_publish.status}. "
                    f"Publish record exists but has no batch records — cleaning up "
                    f"and proceeding with new publish."
                )
                try:
                    if active_publish.publish_type == PublishType.UPDATE.value:
                        self._cleanup_pending_clone_on_update_failure(
                            tenant=tenant,
                            publish_id=active_publish.id,
                            operator=operator,
                        )
                    publish_repo.update_status(
                        publish_id=active_publish.id,
                        tenant=tenant,
                        env=env,
                        status=PublishStatus.FAILED.value,
                        modifier=operator,
                    )
                    logger.info(
                        f"Orphan publish {active_publish.id} cleaned up — "
                        f"marked as FAILED"
                    )
                except Exception as cleanup_error:
                    logger.error(
                        f"Failed to auto-clean orphan publish {active_publish.id}: "
                        f"{cleanup_error}",
                        exc_info=True,
                    )
                    raise PublishConflictError(
                        f"Orphan publish exists for this bot "
                        f"(existing_id={active_publish.id}, "
                        f"status={active_publish.status}) but auto-cleanup failed. "
                        f"Manual cleanup required before creating new publish."
                    ) from cleanup_error
            else:
                # Check if the active publish is stale (exceeded timeout threshold).
                # If stale, resolve it to FAILED and allow the new publish to proceed
                # regardless of whether the publish type matches.
                time_since_modified = publish_repo.now() - active_publish.gmt_modified
                timeout_threshold = timedelta(
                    seconds=DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS
                )
                if time_since_modified > timeout_threshold:
                    logger.warning(
                        f"Stale publish detected: id={active_publish.id}, "
                        f"type={active_publish.publish_type}, "
                        f"status={active_publish.status}, "
                        f"gmt_modified={active_publish.gmt_modified}, "
                        f"stale_for={time_since_modified.total_seconds():.0f}s "
                        f"(threshold={DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS}s). "
                        f"Auto-resolving to FAILED to allow "
                        f"{publish_type.value} publish to proceed."
                    )
                    await self._check_and_handle_timeout(active_publish, tenant)
                    publish_repo.update_status(
                        publish_id=active_publish.id,
                        tenant=tenant,
                        env=env,
                        status=PublishStatus.FAILED.value,
                        modifier=operator,
                    )
                    logger.info(
                        f"Stale publish {active_publish.id} auto-resolved to "
                        f"FAILED — timed-out records processed and publish marked "
                        f"as FAILED"
                    )
                elif active_publish.publish_type != publish_type.value:
                    # Case A: Different publish type, not stale — conflict
                    logger.warning(
                        f"Concurrent publish type mismatch: "
                        f"existing_type={active_publish.publish_type}, "
                        f"requested_type={publish_type.value}, "
                        f"existing_id={active_publish.id}, "
                        f"age={time_since_modified.total_seconds():.0f}s "
                        f"(threshold={DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS}s)"
                    )
                    raise PublishConflictError(
                        f"Cannot create {publish_type.value} publish: "
                        f"bot has active {active_publish.publish_type} publish "
                        f"(id={active_publish.id})"
                    )
                else:
                    # Case B: Same publish type, not stale — idempotent return
                    logger.warning(
                        f"Concurrent publish found: "
                        f"existing_id={active_publish.id}, "
                        f"requested_type={publish_type.value}"
                    )
                    logger.info(
                        f"Returning existing publish {active_publish.id} "
                        f"instead of creating new one"
                    )
                    return self._build_publish_response(active_publish)

        # Step 2.5: Infer device count and derive scale_amount
        # For non-CREATE types, we need the current device count:
        # - SCALE types: to derive scale_amount = |current - target|
        # - Other types: to infer replica_desired for auto-compact
        is_scale_type = publish_type in (PublishType.SCALE_UP, PublishType.SCALE_DOWN)
        scale_amount: int | None = None
        needs_device_count = publish_type != PublishType.CREATE and (
            config is None or not config.replica_desired
        )
        if is_scale_type and config and config.replica_desired:
            # SCALE types: derive scale_amount from replica_desired and current count
            device_repo = self._device_repo
            existing_devices = device_repo.list_by_bot_id(
                bot_id=bot.id, tenant=tenant, env=env
            )
            active_device_count = len(
                [d for d in existing_devices if d.status != "RELEASED"]
            )
            scale_amount = abs(config.replica_desired - active_device_count)
            logger.info(
                f"Derived scale_amount={scale_amount} for "
                f"{publish_type.value}: target={config.replica_desired}, current={active_device_count}"
            )
            # Validate: scale_amount must be positive and direction must match type
            if scale_amount <= 0:
                raise ValueError(
                    f"Invalid scale operation: target={config.replica_desired}, "
                    f"current={active_device_count} — no devices to scale"
                )
            if (
                publish_type == PublishType.SCALE_UP
                and config.replica_desired <= active_device_count
            ):
                raise ValueError(
                    f"SCALE_UP requires target > current: target={config.replica_desired}, "
                    f"current={active_device_count}"
                )
            if (
                publish_type == PublishType.SCALE_DOWN
                and config.replica_desired >= active_device_count
            ):
                raise ValueError(
                    f"SCALE_DOWN requires target < current: target={config.replica_desired}, "
                    f"current={active_device_count}"
                )
        elif needs_device_count:
            from secbaas.community.api.device_manage import DeviceStatus

            if publish_type == PublishType.UPDATE:
                # For UPDATE, use the source bot record's replica_desired
                # (the bot config that was deployed, not the device count)
                replica_desired = bot.replica_desired
                if not replica_desired:
                    raise ValueError(
                        f"Bot {bot.id} has no replica_desired configured. "
                        f"UPDATE publish requires a valid replica_desired value."
                    )
                if config is None:
                    config = PublishConfig(replica_desired=replica_desired)
                else:
                    config.replica_desired = replica_desired
            elif publish_type == PublishType.RESTART:
                device_repo = self._device_repo
                existing_devices = device_repo.list_by_bot_id(
                    bot_id=bot.id, tenant=tenant, env=env
                )

                restart_scope = (
                    config.restart_scope if config else None
                ) or RestartScope.ALL

                include_updating = True
                active_publish = publish_repo.get_active_by_bot_id(
                    bot.id, tenant=tenant, env=env
                )
                if active_publish:
                    logger.info(
                        f"[create_publish] RESTART bot_id={bot.id} has active publish "
                        f"{active_publish.id}, excluding UPDATING devices"
                    )
                    include_updating = False

                if restart_scope == RestartScope.UNHEALTHY:
                    eligible = {
                        DeviceStatus.FAILED.value,
                        DeviceStatus.PENDING.value,
                        DeviceStatus.STOPPED.value,
                    }
                else:
                    eligible = {
                        DeviceStatus.ACTIVE.value,
                        DeviceStatus.FAILED.value,
                        DeviceStatus.PENDING.value,
                        DeviceStatus.STOPPED.value,
                    }
                if include_updating:
                    eligible.add(DeviceStatus.UPDATING.value)

                restart_device_count = len(
                    [d for d in existing_devices if d.status in eligible]
                )

                if restart_device_count == 0:
                    raise ValueError(
                        "No eligible devices found to restart"
                        f" (scope={restart_scope.value}, "
                        f"include_updating={include_updating})"
                    )

                if config is None:
                    config = PublishConfig(replica_desired=restart_device_count)
                else:
                    config.replica_desired = restart_device_count

        # Snapshot config after replica_desired inference (must be AFTER
        # the elif needs_device_count block so corrected values are captured)
        extra_config = config.model_dump(exclude_none=True) if config else {}

        # Step 3: Generate batch configuration
        batch_configs = self._generate_batches(
            publish_type, config, scale_amount=scale_amount
        )
        logger.info(f"Generated {len(batch_configs)} batches for {publish_type.value}")

        # Step 4: Create baas_publish record with status=PENDING
        merge_config = extra_config
        if "stages" not in merge_config:
            # Use defaults from PublishConfig
            defaults = PublishConfig.get_defaults_for_type(publish_type)
            merge_config = {**defaults, **merge_config}

        # Extract top-level fields from config to pass as separate columns
        replica_desired = merge_config.get("replica_desired")
        batch_capacity = merge_config.get("batch_capacity")
        batch_number = merge_config.get("batch_number")
        cooldown_seconds = merge_config.get("cooldown_seconds")
        config_version = merge_config.get("config_version")
        bot_name = merge_config.get("bot_name")

        publish_id = publish_repo.insert_publish(
            tenant=tenant,
            env=env,
            domain=bot.domain,
            bot_id=bot_id,
            publish_type=publish_type.value,
            status=PublishStatus.PENDING.value,
            extra_config=merge_config,
            creator=operator,
            modifier=operator,
            replica_desired=replica_desired,
            batch_capacity=batch_capacity,
            batch_number=batch_number,
            cooldown_seconds=cooldown_seconds,
            config_version=config_version,
            name=bot_name,
        )
        logger.info(f"Created publish record: id={publish_id}")

        # Step 4.5: For UPDATE publish, create new bot record with PENDING status
        if publish_type == PublishType.UPDATE:
            from secbaas.community.api.bot_manage import BotConfig as BotConfigModel

            new_bot_config = None
            if config and config.deploy_config:
                new_bot_config = BotConfigModel(
                    deploy_config=config.deploy_config,
                    callback_timeout_seconds=config.callback_timeout_seconds,
                    auto_approve_publish=config.auto_approve,
                )
            new_bot = await self._bot_service.create_bot_record(
                tenant=tenant,
                source_bot_id=bot_id,
                new_config=new_bot_config,
                operator=operator,
            )
            # Store target_bot_id via PublishConfig field for use at completion
            merge_config["target_bot_id"] = new_bot.id
            publish_repo.update_publish(
                publish_id=publish_id,
                tenant=tenant,
                env=env,
                extra_config=merge_config,
                modifier=operator,
            )
            logger.info(
                f"Created PENDING bot record for UPDATE publish: "
                f"target_bot_id={new_bot.id}, publish_id={publish_id}"
            )

        # Step 5: Create baas_publish_batch records
        batch_repo = self._publish_batch_repo
        batch_records: list[PublishBatchRecord] = []
        for batch_cfg in batch_configs:
            batch_id = batch_repo.insert_batch(
                tenant=tenant,
                env=env,
                domain=bot.domain,
                publish_id=publish_id,
                bot_id=bot_id,
                batch_index=batch_cfg.batch_index,
                batch_capacity=batch_cfg.batch_capacity,
                status=BatchStatus.PENDING.value,
                creator=operator,
                modifier=operator,
                extra_config=PublishBatchConfig(
                    stage=batch_cfg.stage,
                    cooldown_seconds=batch_cfg.cooldown_seconds,
                    device_count=batch_cfg.device_count,
                ).model_dump(),
            )
            batch_record = batch_repo.get_by_id(batch_id, tenant, env)
            if batch_record:
                batch_records.append(batch_record)
        logger.info(
            f"Created {len(batch_configs)} batch records for publish {publish_id}"
        )

        # Step 5.5: Pre-create device-level publish records (with PENDING status)
        self._create_device_records_for_publish(
            tenant=tenant,
            env=env,
            publish_id=publish_id,
            publish_type=publish_type,
            bot_record=bot,
            batch_records=batch_records,
            operator=operator,
        )

        # Step 6: Return publish response
        publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
        if publish_record is None:
            raise PublishNotFoundError(publish_id)
        current_stage = self._get_current_stage(tenant, publish_id)
        return PublishResponse(
            id=publish_record.id,
            bot_id=publish_record.bot_id,
            publish_type=publish_record.publish_type,
            status=publish_record.status,
            stage=current_stage,
            extra_config=_extra_config_to_publish_config(publish_record.extra_config),
            creator=publish_record.creator,
            modifier=publish_record.modifier,
            gmt_create=publish_record.gmt_create,
            gmt_modified=publish_record.gmt_modified,
        )

    def _generate_batches(
        self,
        publish_type: PublishType,
        config: PublishConfig | None = None,
        scale_amount: int | None = None,
    ) -> list[BatchConfig]:
        """Generate batch configurations for publish type.

        Per D-01a: Type-specific pipeline generation.

        Pipeline Stage Map:
        ┌─────────────────┬──────────────────────────────────────────────────────────┐
        │ PublishType     │ Stages                                                    │
        ├─────────────────┼──────────────────────────────────────────────────────────┤
        │ CREATE          │ PREPUB → GRAY → PROD_FIRST_BATCH → PROD_OTHER_BATCH      │
        │ UPDATE          │ PREPUB → GRAY → PROD_FIRST_BATCH → PROD_OTHER_BATCH      │
        │ RESTART         │ PROD_FIRST_BATCH → PROD_OTHER_BATCH                      │
        │ SCALE_UP        │ direct (mapped to PROD_FIRST_BATCH)                      │
        │ SCALE_DOWN      │ direct (mapped to PROD_FIRST_BATCH)                      │
        │ DESTROY         │ direct (mapped to PROD_FIRST_BATCH)                      │
        └─────────────────┴──────────────────────────────────────────────────────────┘

        Auto-Compact Logic:
        When device_count <= stage_count, the pipeline is compacted to reduce stages:

        ┌────────────────┬─────────────────────────────────────────────────────────┐
        │ Device Count   │ Effective Stages                                        │
        ├────────────────┼─────────────────────────────────────────────────────────┤
        │ 1              │ PROD_FIRST_BATCH only (single stage)                    │
        │ 2              │ Last 2 stages from pipeline                             │
        │ 3              │ Last 3 stages from pipeline                             │
        │ 4+             │ Full 4-stage pipeline (for CREATE/UPDATE)               │
        ├────────────────┼─────────────────────────────────────────────────────────┤
        │ SCALE types    │ No auto-compact; uses batch_capacity chunking instead   │
        └────────────────┴─────────────────────────────────────────────────────────┘

        Device Distribution (when replica_desired is set):
        - Devices are evenly distributed across stages
        - First N stages get base_devices + 1 for remainder distribution
        - Each stage gets at least floor(device_count / stage_count) devices

        Batch Splitting:
        - Each stage can have multiple batches if device_count > batch_capacity
        - Example: 12 devices with batch_capacity=5 → 3 batches (5, 5, 2)

        Args:
            publish_type: Type of publish operation
            config: Optional publish configuration with stage overrides
            scale_amount: For SCALE types, the number of devices to add/remove
                         (auto-derived in create_publish from |target - current|)

        Returns:
            List of BatchConfig with batch_index, stage, batch_capacity, cooldown, device_count
        """
        batches: list[BatchConfig] = []
        batch_index = 0

        # Preserve original config's top-level fields
        original_replica_desired = config.replica_desired if config else None
        original_batch_capacity = config.batch_capacity if config else None

        # Use provided config or defaults for type
        if config is None or not config.stages:
            defaults = PublishConfig.get_defaults_for_type(publish_type)
            config = PublishConfig.model_validate(defaults)
            # Restore original top-level fields that may have been set by caller
            if original_replica_desired:
                config.replica_desired = original_replica_desired
            if original_batch_capacity:
                config.batch_capacity = original_batch_capacity

        # Per D-01a mapping
        if publish_type in (PublishType.CREATE, PublishType.UPDATE):
            # 4-stage pipeline: PREPUB, GRAY, PROD_FIRST_BATCH, PROD_OTHER_BATCH
            stages = [
                ("PREPUB", PublishStage.PREPUB.value),
                ("GRAY", PublishStage.GRAY.value),
                ("PROD_FIRST_BATCH", PublishStage.PROD_FIRST_BATCH.value),
                ("PROD_OTHER_BATCH", PublishStage.PROD_OTHER_BATCH.value),
            ]
        elif publish_type == PublishType.RESTART:
            # 2-stage pipeline: PROD_FIRST_BATCH, PROD_OTHER_BATCH
            stages = [
                ("PROD_FIRST_BATCH", PublishStage.PROD_FIRST_BATCH.value),
                ("PROD_OTHER_BATCH", PublishStage.PROD_OTHER_BATCH.value),
            ]
        elif publish_type in (
            PublishType.SCALE_UP,
            PublishType.SCALE_DOWN,
            PublishType.STOP,
            PublishType.UPDATE_DEVICE,
        ):
            # SCALE_UP, SCALE_DOWN, STOP, UPDATE_DEVICE: direct execution
            stages = [("direct", PublishStage.PROD_FIRST_BATCH.value)]
        else:
            # DESTROY: direct execution with multiple batches
            stages = [("direct", PublishStage.PROD_FIRST_BATCH.value)]

        # Calculate total device count for auto-compact
        total_device_count = self._calculate_total_devices(
            config, stages, scale_amount=scale_amount
        )

        # Auto-compact: progressive stages based on device count
        # 1 device → 1 stage (PROD_FIRST_BATCH), 2 devices → 2 stages, etc.
        # When replica_desired is set, always distribute 1 device per stage
        # SCALE types: do NOT distribute devices per stage, use batch_capacity instead
        effective_stages = stages
        is_scale_type = publish_type in (PublishType.SCALE_UP, PublishType.SCALE_DOWN)
        distribute_devices = total_device_count is not None and not is_scale_type

        if total_device_count and total_device_count < len(stages):
            if total_device_count == 1:
                # Single device: use PROD_FIRST_BATCH specifically
                # fmt: off
                prod_first_batch_stage = next(
                    (
                        s
                        for s in stages
                        if s[1] == PublishStage.PROD_FIRST_BATCH.value
                    ),
                    stages[0],
                )
                # fmt: on
                effective_stages = [prod_first_batch_stage]
            else:
                # Multiple devices: use last N stages (most production-relevant)
                effective_stages = stages[-total_device_count:]

        # Distribute devices across stages when replica_desired is set
        # For distribute_devices: calculate per-stage device count
        if distribute_devices and total_device_count:
            stage_count = len(effective_stages)
            base_devices = total_device_count // stage_count
            extra_devices = total_device_count % stage_count
            # First 'extra_devices' stages get base_devices + 1, rest get base_devices
            per_stage_devices = [
                base_devices + 1 if i < extra_devices else base_devices
                for i in range(stage_count)
            ]
        else:
            per_stage_devices = None

        for stage_idx, (stage_key, stage_value) in enumerate(effective_stages):
            stage_cfg = config.stages.get(stage_key, StageConfig())

            if distribute_devices and per_stage_devices:
                # Use pre-calculated per-stage device count
                device_count = per_stage_devices[stage_idx]
            elif is_scale_type:
                # SCALE types use total_device_count directly from scale_amount
                device_count = total_device_count or 0
            else:
                device_count = stage_cfg.device_count or 1

            # Skip batches with zero devices
            if device_count <= 0:
                continue

            # Prioritize config.batch_capacity (caller override) over stage defaults
            batch_capacity = config.batch_capacity or stage_cfg.batch_capacity or 5
            cooldown = stage_cfg.cooldown_seconds

            # All types support multiple batches per stage based on batch_capacity
            devices_remaining = device_count
            while devices_remaining > 0:
                current_batch_capacity = min(batch_capacity, devices_remaining)
                batches.append(
                    BatchConfig(
                        batch_index=batch_index,
                        stage=stage_value,
                        batch_capacity=current_batch_capacity,
                        cooldown_seconds=cooldown if not is_scale_type else 0,
                        device_count=current_batch_capacity,
                    )
                )
                batch_index += 1
                devices_remaining -= current_batch_capacity

        return batches

    def _calculate_total_devices(
        self,
        config: PublishConfig,
        stages: list[tuple[str, str]],
        scale_amount: int | None = None,
    ) -> int | None:
        """Calculate total device count for auto-compact decision.

        Priority:
        1. scale_amount parameter (for SCALE_UP/DOWN) - the delta to add/remove
        2. config.replica_desired (for CREATE/UPDATE)
        3. Default: 1 (stage device_count defaults are production-scale configs,
           not actual device counts; summing them would over-generate batches)

        IMPORTANT: Stage default device_counts (e.g., PREPUB=2, GRAY=4) are
        production-scale batch sizes, NOT target device counts. Summing them
        would cause auto-compact to malfunction for small bots. When
        replica_desired is not set, default to 1 to ensure proper auto-compact.
        """
        # Check scale_amount first (used by SCALE_UP/DOWN) - the actual delta
        if scale_amount:
            return scale_amount

        # Check replica_desired (used by CREATE/UPDATE)
        if config.replica_desired:
            return config.replica_desired

        # Default: 1 device. Stage default device_counts are batch-size configs,
        # NOT actual device counts. Summing PREPUB=2 + GRAY=4 = 6 would cause
        # _generate_batches to create a full 4-stage pipeline instead of 1 batch.
        return 1

    def _create_device_records_for_publish(
        self,
        tenant: str,
        env: str,
        publish_id: int,
        publish_type: PublishType,
        bot_record: BotRecord,
        batch_records: list[PublishBatchRecord],
        operator: str,
    ) -> None:
        """Pre-create device-level PublishRecordRecord entries at create_publish time.

        For each batch, selects eligible devices and creates PENDING status records.
        This replaces the dynamic insert_record() calls previously done in each
        _execute_*_batch handler.

        Per-type device selection:
        - CREATE: existing PENDING devices for the bot
        - UPDATE: ACTIVE+FAILED+PENDING+STOPPED devices for the bot
        - RESTART: devices per scope (all/unhealthy)
        - SCALE_UP: new devices via DeviceService.create_device()
        - SCALE_DOWN: oldest ACTIVE devices
        - DESTROY: ACTIVE devices
        """
        from secbaas.community.api.device_manage import (
            DeviceConfig,
            DeviceCreate,
            DeviceStatus,
        )

        device_repo = self._device_repo
        record_repo = self._publish_record_repo
        domain = bot_record.domain
        bot_id = bot_record.id

        eligible_devices: list = []
        event_type: str = ""

        if publish_type == PublishType.CREATE:
            all_devices = device_repo.list_by_bot_id(
                bot_id=bot_id, tenant=tenant, env=env
            )
            eligible_devices = [
                d for d in all_devices if d.status == DeviceStatus.PENDING.value
            ]
            event_type = PublishEventType.CREATE.value
            logger.info(
                f"[create_device_records] CREATE publish_id={publish_id} "
                f"found {len(eligible_devices)} PENDING devices"
            )

        elif publish_type == PublishType.UPDATE:
            all_devices = device_repo.list_by_bot_id(
                bot_id=bot_id, tenant=tenant, env=env
            )
            eligible_devices = [
                d
                for d in all_devices
                if d.status
                in (
                    DeviceStatus.ACTIVE.value,
                    DeviceStatus.FAILED.value,
                    DeviceStatus.PENDING.value,
                    DeviceStatus.STOPPED.value,
                )
            ]
            event_type = PublishEventType.UPDATE.value
            logger.info(
                f"[create_device_records] UPDATE publish_id={publish_id} "
                f"found {len(eligible_devices)} ACTIVE+FAILED+PENDING+STOPPED devices"
            )

        elif publish_type == PublishType.RESTART:
            all_devices = device_repo.list_by_bot_id(
                bot_id=bot_id, tenant=tenant, env=env
            )

            publish_repo = self._publish_repo
            publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
            restart_scope = RestartScope.ALL
            if publish_record and publish_record.extra_config:
                try:
                    scope_cfg = PublishConfig.model_validate(
                        publish_record.extra_config
                    )
                    restart_scope = scope_cfg.restart_scope or RestartScope.ALL
                except Exception:
                    pass

            if restart_scope == RestartScope.UNHEALTHY:
                eligible_statuses = {
                    DeviceStatus.FAILED.value,
                    DeviceStatus.STOPPED.value,
                    DeviceStatus.PENDING.value,
                }
            else:
                eligible_statuses = {
                    DeviceStatus.ACTIVE.value,
                    DeviceStatus.FAILED.value,
                    DeviceStatus.PENDING.value,
                    DeviceStatus.STOPPED.value,
                }
            eligible_devices = [d for d in all_devices if d.status in eligible_statuses]
            event_type = PublishEventType.RESTART.value
            logger.info(
                f"[create_device_records] RESTART publish_id={publish_id} "
                f"scope={restart_scope.value} found {len(eligible_devices)} devices"
            )

        elif publish_type == PublishType.SCALE_UP:
            if not bot_record.template_uuid:
                logger.error(
                    f"[create_device_records] Bot {bot_id} has no template_uuid "
                    f"for SCALE_UP publish_id={publish_id}"
                )
                return

            template = self._template_service.get_online_template_by_uuid(
                tenant, bot_record.template_uuid
            )
            if not template:
                logger.error(
                    f"[create_device_records] Template {bot_record.template_uuid} "
                    f"not found for SCALE_UP publish_id={publish_id}"
                )
                return

            bot_config = bot_record.config

            total_new_devices = sum(b.batch_capacity for b in batch_records)

            event_type = PublishEventType.CREATE.value
            logger.info(
                f"[create_device_records] SCALE_UP publish_id={publish_id} "
                f"creating {total_new_devices} new devices"
            )

            for i in range(total_new_devices):
                device_data = DeviceCreate(
                    domain=domain,
                    operator=operator,
                    extra_config=DeviceConfig(
                        template_uuid=bot_record.template_uuid,
                        deploy_config=bot_config.deploy_config if bot_config else None,
                    ),
                )
                device = self._device_service.create_device(
                    tenant=tenant, data=device_data
                )
                rel_repo = self._rel_repo
                rel_repo.insert_rel(
                    bot_id=bot_id,
                    device_uuid=device.device_uuid,
                    tenant=tenant,
                    env=env,
                    domain=domain,
                    creator=operator,
                    modifier=operator,
                )
                eligible_devices.append(device)

        elif publish_type == PublishType.SCALE_DOWN:
            all_devices = device_repo.list_by_bot_id(
                bot_id=bot_id, tenant=tenant, env=env
            )
            eligible_devices = sorted(
                [d for d in all_devices if d.status == DeviceStatus.ACTIVE.value],
                key=lambda d: d.id,
            )
            event_type = PublishEventType.DESTROY.value
            logger.info(
                f"[create_device_records] SCALE_DOWN publish_id={publish_id} "
                f"found {len(eligible_devices)} ACTIVE devices"
            )

        elif publish_type == PublishType.DESTROY:
            all_devices = device_repo.list_by_bot_id(
                bot_id=bot_id, tenant=tenant, env=env
            )
            eligible_devices = sorted(
                [d for d in all_devices if d.status == DeviceStatus.ACTIVE.value],
                key=lambda d: d.id,
            )
            event_type = PublishEventType.DESTROY.value
            logger.info(
                f"[create_device_records] DESTROY publish_id={publish_id} "
                f"found {len(eligible_devices)} ACTIVE devices"
            )

        elif publish_type == PublishType.STOP:
            all_devices = device_repo.list_by_bot_id(
                bot_id=bot_id, tenant=tenant, env=env
            )
            eligible_devices = sorted(
                [d for d in all_devices if d.status == DeviceStatus.ACTIVE.value],
                key=lambda d: d.id,
            )
            event_type = PublishEventType.STOP.value
            logger.info(
                f"[create_device_records] STOP publish_id={publish_id} "
                f"found {len(eligible_devices)} ACTIVE devices"
            )

        elif publish_type == PublishType.UPDATE_DEVICE:
            all_devices = device_repo.list_by_bot_id(
                bot_id=bot_id, tenant=tenant, env=env
            )
            publish_repo = self._publish_repo
            publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
            target_uuids = []
            if publish_record and publish_record.extra_config:
                try:
                    cfg = PublishConfig.model_validate(publish_record.extra_config)
                    target_uuids = cfg.target_device_uuids or []
                except Exception:
                    logger.warning(
                        f"[create_device_records] Failed to parse "
                        f"target_device_uuids for UPDATE_DEVICE publish_id={publish_id}"
                    )
            # Filter by target UUIDs and eligible statuses
            eligible_statuses = {
                DeviceStatus.ACTIVE.value,
                DeviceStatus.FAILED.value,
                DeviceStatus.PENDING.value,
                DeviceStatus.STOPPED.value,
            }
            device_map = {d.device_uuid: d for d in all_devices}
            eligible_devices = [
                device_map[uuid]
                for uuid in target_uuids
                if uuid in device_map and device_map[uuid].status in eligible_statuses
            ]
            event_type = PublishEventType.UPDATE_DEVICE.value
            logger.info(
                f"[create_device_records] UPDATE_DEVICE publish_id={publish_id} "
                f"targeted {len(target_uuids)} devices, "
                f"found {len(eligible_devices)} eligible"
            )

        if not eligible_devices:
            logger.warning(
                f"[create_device_records] No eligible devices for "
                f"publish_id={publish_id} type={publish_type.value}"
            )
            raise ValueError(
                f"No eligible devices for {publish_type.value} publish "
                f"(publish_id={publish_id}): cannot create device-level records"
            )

        sorted_batches = sorted(batch_records, key=lambda b: b.batch_index)
        sorted_devices = sorted(eligible_devices, key=lambda d: d.id)

        device_idx = 0
        for batch in sorted_batches:
            batch_cap = batch.batch_capacity
            batch_devices = sorted_devices[device_idx : device_idx + batch_cap]
            device_idx += batch_cap

            for device in batch_devices:
                record_repo.insert_record(
                    tenant=tenant,
                    env=env,
                    domain=domain,
                    device_id=device.id,
                    bot_id=bot_id,
                    publish_id=publish_id,
                    batch_id=batch.id,
                    event_type=event_type,
                    result_status=PublishRecordResult.PENDING.value,
                    result_message=None,
                    creator=operator,
                    modifier=operator,
                )

        logger.info(
            f"[create_device_records] publish_id={publish_id} type={publish_type.value} "
            f"created {len(sorted_devices[:device_idx])} PENDING records "
            f"across {len(batch_records)} batches"
        )

    def _get_current_stage(self, tenant: str, publish_id: int) -> str | None:
        """Get current pipeline stage from the first non-completed batch.

        Returns the stage of the first batch that hasn't completed.
        Returns None if all batches are complete or no batches exist.
        """
        env = get_current_env()
        batch_repo = self._publish_batch_repo
        batches = batch_repo.list_by_publish_id(publish_id, tenant, env)

        if not batches:
            return None

        # Find first non-COMPLETED batch
        for batch in batches:
            if batch.status != BatchStatus.COMPLETED.value:
                return batch.stage

        # All batches complete
        return PublishStage.SUCCESS.value

    def _get_pending_batches(
        self, tenant: str, publish_id: int
    ) -> tuple[str | None, list[PublishBatchRecord]]:
        """Get pending batches (first non-COMPLETED stage) in ONE query.

        Returns (current_stage, batches) tuple.
        This avoids the circular dependency of querying batches twice.
        """
        env = get_current_env()
        batch_repo = self._publish_batch_repo
        all_batches = batch_repo.list_by_publish_id(publish_id, tenant, env)

        if not all_batches:
            return None, []

        # Find first non-COMPLETED batch to determine current stage
        pending_stage = None
        for batch in all_batches:
            if batch.status != BatchStatus.COMPLETED.value:
                pending_stage = batch.stage
                break

        if pending_stage is None:
            # All complete
            return PublishStage.SUCCESS.value, []

        # Filter batches by the pending stage
        pending_batches = [b for b in all_batches if b.stage == pending_stage]
        return pending_stage, pending_batches

    def _check_all_batches_complete(self, tenant: str, publish_id: int) -> bool:
        """Check if all batches for a publish are processed (no PENDING or RUNNING).

        Returns True if no batches are PENDING or RUNNING, meaning all stages complete.
        Returns False if there are still batches to process.
        """
        env = get_current_env()
        batch_repo = self._publish_batch_repo
        batches = batch_repo.list_by_publish_id(publish_id, tenant, env)

        if not batches:
            return False

        # Check if any batch is still pending or running
        for batch in batches:
            if batch.status in (BatchStatus.PENDING.value, BatchStatus.RUNNING.value):
                return False

        return True

    def _should_auto_complete(
        self, tenant: str, publish_id: int, config: PublishConfig
    ) -> bool:
        """Check if publish should auto-complete.

        Returns True if:
        - auto_complete config is True
        - All batches are COMPLETED (no PENDING, RUNNING, or FAILED)
        """
        if not config.auto_complete:
            return False

        env = get_current_env()
        batch_repo = self._publish_batch_repo
        batches = batch_repo.list_by_publish_id(publish_id, tenant, env)

        if not batches:
            return False

        # Auto-complete only when ALL batches are COMPLETED
        # - PENDING/RUNNING means there are more stages to execute
        # - FAILED means something went wrong, need manual intervention
        for batch in batches:
            if batch.status != BatchStatus.COMPLETED.value:
                return False

        return True

    def _get_publish_and_bot_record(
        self, tenant: str, publish_id: int
    ) -> tuple[PublishRecord | None, BotRecord | None]:
        """Get publish record and bot by ID with tenant verification.

        Returns (record, bot) tuple. Returns (None, None) if not found
        or tenant mismatch. The bot read is always needed for tenant
        verification, so we return it to avoid callers re-fetching it.

        For DESTROY publishes, the bot may have been soft-deleted after
        completion. In that case, we still need to find it for tenant
        verification — a soft-deleted bot matching tenant+env is still
        a valid ownership proof.
        """
        env = get_current_env()
        repo = self._publish_repo
        publish_record = repo.get_by_id(publish_id, tenant=tenant, env=env)

        if publish_record is None:
            return None, None

        bot_repo = self._bot_repo
        bot_record = bot_repo.get_by_id_including_deleted(
            publish_record.bot_id, tenant=tenant, env=env
        )
        if bot_record is None:
            logger.warning(
                f"Tenant mismatch for publish: tenant={tenant}, publish={publish_id}"
            )
            return None, None

        return publish_record, bot_record

    async def get_publish_bot_uuid(self, *, tenant: str, publish_id: int) -> str:
        """Resolve publish_id to bot_uuid with tenant verification.

        Two-hop lookup: publish_id -> PublishRecord -> BotRecord.bot_uuid.
        Uses get_by_id_including_deleted for the bot lookup to handle DESTROY
        publishes where the bot may have been soft-deleted.

        Raises PublishNotFoundError if the publish does not exist, the tenant
        does not match, or the associated bot record cannot be found.
        """
        env = get_current_env()
        publish_record = self._publish_repo.get_by_id(
            publish_id, tenant=tenant, env=env
        )
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        bot_record = self._bot_repo.get_by_id_including_deleted(
            publish_record.bot_id, tenant=tenant, env=env
        )
        if bot_record is None:
            raise PublishNotFoundError(publish_id)

        return bot_record.bot_uuid

    def _build_publish_response(self, publish_record: PublishRecord) -> PublishResponse:
        """Build PublishResponse from a publish record without fetching bot_record.

        Use this when the caller already has publish_record and only needs the
        response — avoids the extra DB query to fetch bot_record for tenant
        verification (which was already done earlier in the call chain).
        """
        current_stage = self._get_current_stage(
            publish_record.tenant, publish_record.id
        )
        return PublishResponse(
            id=publish_record.id,
            bot_id=publish_record.bot_id,
            publish_type=publish_record.publish_type,
            status=publish_record.status,
            stage=current_stage,
            extra_config=_extra_config_to_publish_config(publish_record.extra_config),
            creator=publish_record.creator,
            modifier=publish_record.modifier,
            gmt_create=publish_record.gmt_create,
            gmt_modified=publish_record.gmt_modified,
        )

    async def _refresh_publish_response(
        self, tenant: str, publish_id: int
    ) -> PublishResponse:
        """Re-read publish record and build response (skips bot fetch).

        Use after status updates where the publish record needs a fresh read
        but bot_record was already verified earlier in the call chain.
        Saves 1 DB query vs calling get_publish() which also fetches bot_record.
        """
        env = get_current_env()
        repo = self._publish_repo
        publish_record = repo.get_by_id(publish_id, tenant=tenant, env=env)
        if publish_record is None:
            raise PublishNotFoundError(publish_id)
        return self._build_publish_response(publish_record)

    async def get_publish(self, tenant: str, publish_id: int) -> PublishResponse | None:
        """Get publish by ID with tenant verification (full, for API responses)."""
        env = get_current_env()
        logger.info(
            f"[get_publish] tenant={tenant}, env={env}, publish_id={publish_id}"
        )

        publish_record, bot_record = self._get_publish_and_bot_record(
            tenant, publish_id
        )
        if publish_record is None:
            return None

        current_stage = self._get_current_stage(tenant, publish_id)
        return PublishResponse(
            id=publish_record.id,
            bot_id=publish_record.bot_id,
            publish_type=publish_record.publish_type,
            status=publish_record.status,
            stage=current_stage,
            extra_config=_extra_config_to_publish_config(publish_record.extra_config),
            creator=publish_record.creator,
            modifier=publish_record.modifier,
            gmt_create=publish_record.gmt_create,
            gmt_modified=publish_record.gmt_modified,
        )

    async def approve_stage(
        self,
        tenant: str,
        publish_id: int,
        operator: str,
        comment: str | None = None,
        _called_internally: bool = False,
    ) -> PublishResponse:
        """
        Approve current stage and proceed.

        Per D-02: Handles both initial approval (PENDING -> ACTIVE) and
        stage-gate approval (APPROVING -> ACTIVE).

        Per D-10: Approver can also revoke after approval.

        Auto-execute: When transitioning from PENDING to ACTIVE, automatically
        executes all stages until completion or approval gate.

        Args:
            tenant: Tenant name for isolation
            publish_id: Publish to approve
            operator: User ID of operator
            comment: Optional approval comment
            _called_internally: Internal flag — when True, bypasses
                the auto_approve guard. Set by _auto_approve_publish loop.
        """
        env = get_current_env()
        logger.info(
            f"[approve_stage] publish_id={publish_id}, operator={operator}, "
            f"comment={comment}"
        )

        # Get publish and verify (lightweight — no bot status calc needed)
        publish_record, bot_record = self._get_publish_and_bot_record(
            tenant, publish_id
        )
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        # Guard: ignore manual /approve calls when auto_approve is active
        extra_config = publish_record.extra_config or {}
        if extra_config.get("auto_approve") and not _called_internally:
            logger.info(
                f"[approve_stage] publish_id={publish_id} auto_approve is active, "
                f"ignoring manual approve call"
            )
            return await self._refresh_publish_response(tenant, publish_id)

        current_status = publish_record.status

        if current_status == PublishStatus.SUCCESS.value:
            logger.info(
                f"[approve_stage] publish_id={publish_id} already SUCCESS, no-op"
            )
            return await self._refresh_publish_response(tenant, publish_id)

        if current_status == PublishStatus.ACTIVE.value:
            logger.info(
                f"[approve_stage] publish_id={publish_id} already ACTIVE, "
                f"continuing execution"
            )
            await self._auto_execute_stages(tenant, publish_id, operator)
            return await self._refresh_publish_response(tenant, publish_id)

        if current_status == PublishStatus.APPROVING.value:
            logger.info(
                f"[approve_stage] publish_id={publish_id} APPROVING, "
                f"transitioning to ACTIVE and continuing execution"
            )
            new_status = self._get_next_status(current_status, "approve")
            if new_status is None:
                raise ValueError(
                    f"No valid transition from {current_status} with action 'approve'"
                )
            repo = self._publish_repo
            repo.update_status(
                publish_id=publish_id,
                tenant=tenant,
                env=env,
                status=new_status,
                modifier=operator,
            )
            logger.info(
                f"Publish {publish_id} approved by {operator}, new status: {new_status}"
            )
            await self._auto_execute_stages(tenant, publish_id, operator)
            return await self._refresh_publish_response(tenant, publish_id)

        # Validate state transition
        if not self._can_transition(current_status, "approve"):
            raise ValueError(
                f"Cannot approve publish in status '{current_status}'. "
                f"Expected status: PENDING or APPROVING"
            )

        # Update status
        new_status = self._get_next_status(current_status, "approve")
        if new_status is None:
            raise ValueError(
                f"No valid transition from {publish_record.status} with action 'approve'"
            )
        repo = self._publish_repo
        repo.update_status(
            publish_id=publish_id,
            tenant=tenant,
            env=env,
            status=new_status,
            modifier=operator,
        )

        logger.info(
            f"Publish {publish_id} approved by {operator}, new status: {new_status}"
        )

        # Auto-execute when transitioning to ACTIVE (both PENDING→ACTIVE and APPROVING→ACTIVE)
        if new_status == PublishStatus.ACTIVE.value:
            logger.info(
                f"[approve_stage] Auto-executing stage for publish {publish_id}"
            )
            await self._auto_execute_stages(tenant, publish_id, operator)

        return await self._refresh_publish_response(tenant, publish_id)

    async def _auto_execute_stages(
        self, tenant: str, publish_id: int, operator: str
    ) -> None:
        """Dispatch current stage's batches and return.

        With async start hooks, execute_stage() returns immediately after
        dispatching devices. Subsequent stage execution is triggered by the
        callback handler when the current stage completes.
        For fully synchronous batches (DESTROY, SCALE_DOWN, all no-hook),
        execute_stage() handles stage advancement inline.
        """
        publish_record, bot_record = self._get_publish_and_bot_record(
            tenant, publish_id
        )

        if (
            publish_record is None
            or publish_record.status != PublishStatus.ACTIVE.value
        ):
            return

        # Check if there are pending batches
        current_stage, batches = self._get_pending_batches(tenant, publish_id)
        if not batches:
            return

        # Execute current stage (pass pre-fetched data to avoid redundant DB queries)
        try:
            await self.execute_stage(
                tenant,
                publish_id,
                operator,
                publish_record=publish_record,
                bot_record=bot_record,
                current_stage=current_stage,
                batches=batches,
            )
        except Exception as e:
            logger.error(
                f"Auto-execute failed for publish {publish_id}: {e}",
                exc_info=True,
            )

    async def reject_publish(
        self, tenant: str, publish_id: int, operator: str, reason: str
    ) -> PublishResponse:
        """
        Reject publish at any approval gate.

        Per D-02, D-10: Can reject from PENDING or APPROVING status.
        Marks publish as REJECTED - no further progress allowed.

        Note: For DESTROY publishes, the bot remains in DESTROYING status.
        The publish can be re-approved to continue the destruction.
        Bot status restoration is intentionally NOT performed since DESTROY
        is a terminal operation that should eventually complete.
        """
        env = get_current_env()
        logger.info(
            f"[reject_publish] publish_id={publish_id}, operator={operator}, "
            f"reason={reason}"
        )

        # Get publish and verify
        publish_record, bot_record = self._get_publish_and_bot_record(
            tenant, publish_id
        )
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        # Validate state transition (can reject from PENDING or APPROVING)
        if publish_record.status not in ("PENDING", "APPROVING"):
            raise ValueError(
                f"Cannot reject publish in status '{publish_record.status}'. "
                f"Expected status: PENDING or APPROVING"
            )

        # Update status to REJECTED
        repo = self._publish_repo
        repo.update_status(
            publish_id=publish_id,
            tenant=tenant,
            env=env,
            status="REJECTED",
            modifier=operator,
        )

        logger.info(f"Publish {publish_id} rejected by {operator}: {reason}")

        return await self._refresh_publish_response(tenant, publish_id)

    async def revoke_publish(
        self, tenant: str, publish_id: int, operator: str, reason: str | None = None
    ) -> PublishResponse:
        """
        Revoke publish after approval but before next stage begins.

        Per D-10: Can only revoke from APPROVING status (after approval received,
        before execution resumes). Marks publish as REVOKED.

        Note: For DESTROY publishes, the bot remains in DESTROYING status.
        The publish can be re-approved to continue the destruction.
        Bot status restoration is intentionally NOT performed since DESTROY
        is a terminal operation that should eventually complete.
        """
        env = get_current_env()
        logger.info(
            f"[revoke_publish] publish_id={publish_id}, operator={operator}, "
            f"reason={reason}"
        )

        # Get publish and verify
        publish_record, bot_record = self._get_publish_and_bot_record(
            tenant, publish_id
        )
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        # Validate state transition (can only revoke from APPROVING)
        if publish_record.status != "APPROVING":
            raise ValueError(
                f"Cannot revoke publish in status '{publish_record.status}'. "
                f"Expected status: APPROVING"
            )

        # Update status to REVOKED
        repo = self._publish_repo
        repo.update_status(
            publish_id=publish_id,
            tenant=tenant,
            env=env,
            status="REVOKED",
            modifier=operator,
        )

        logger.info(f"Publish {publish_id} revoked by {operator}: {reason}")

        return await self._refresh_publish_response(tenant, publish_id)

    async def retry_publish(
        self,
        tenant: str,
        publish_id: int,
        operator: str,
        request_id: str,
        config: PublishConfig | None = None,
    ) -> PublishResponse:
        """Retry a failed publish by creating a new publish.

        Creates a new publish with the same bot_id, publish_type, and config
        as the original failed publish. The new publish starts in PENDING status.

        Args:
            tenant: Tenant name for isolation
            publish_id: ID of the FAILED publish to retry
            operator: User retrying the publish
            request_id: Request ID for correlation
            config: Optional new config (uses original if not provided)

        Returns:
            PublishResponse for the newly created publish

        Raises:
            PublishNotFoundError: Original publish not found
            ValueError: Original publish is not in FAILED status
            ValueError: Bot has concurrent active publish
        """
        logger.info(
            f"[retry_publish] original_publish_id={publish_id}, operator={operator}, "
            f"request_id={request_id}"
        )

        original, _ = self._get_publish_and_bot_record(tenant, publish_id)
        if original is None:
            raise PublishNotFoundError(publish_id)

        # Verify original is in FAILED status
        if original.status != PublishStatus.FAILED.value:
            raise ValueError(
                f"Cannot retry publish in status '{original.status}'. "
                f"Retry is only valid for FAILED publishes. "
                f"Create a new publish to retry from other terminal states."
            )

        # Create new publish with same parameters
        new_publish = await self.create_publish(
            tenant=tenant,
            bot_id=original.bot_id,
            publish_type=PublishType(original.publish_type),
            operator=operator,
            request_id=request_id,
            config=config or _extra_config_to_publish_config(original.extra_config),
        )

        logger.info(
            f"[retry_publish] Created new publish {new_publish.id} "
            f"to retry failed publish {publish_id}"
        )

        return new_publish

    async def list_publishes(
        self,
        tenant: str,
        bot_id: int | None = None,
        status: PublishStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[PublishResponse]:
        """List publishes for tenant with optional filtering."""
        env = get_current_env()
        logger.info(
            f"[list_publishes] tenant={tenant}, env={env}, bot={bot_id}, status={status}"
        )

        repo = self._publish_repo
        records = (
            repo.list_by_bot_id(bot_id=bot_id, tenant=tenant, env=env) if bot_id else []
        )

        results = []
        for publish_record in records:
            # Verify tenant
            bot = await self._bot_service.get_bot(
                tenant=tenant, bot_id=publish_record.bot_id, include_status=False
            )
            if bot is None:
                continue

            if status is not None and publish_record.status != status.value:
                continue

            current_stage = self._get_current_stage(tenant, publish_record.id)
            results.append(
                PublishResponse(
                    id=publish_record.id,
                    bot_id=publish_record.bot_id,
                    publish_type=publish_record.publish_type,
                    status=publish_record.status,
                    stage=current_stage,
                    extra_config=_extra_config_to_publish_config(
                        publish_record.extra_config
                    ),
                    creator=publish_record.creator,
                    modifier=publish_record.modifier,
                    gmt_create=publish_record.gmt_create,
                    gmt_modified=publish_record.gmt_modified,
                )
            )

        # Simple pagination
        start = (page - 1) * page_size
        end = start + page_size

        return results[start:end]

    async def list_publishes_by_bot_uuid(
        self,
        tenant: str,
        bot_uuid: str,
    ) -> list[BotPublishSummary]:
        """List every publish workflow tied to a bot_uuid, newest first.

        Backs ``GET /api/v1/bots/{bot_uuid}/publishes``. A bot_uuid may map to
        several bot records (distinct statuses across its lifecycle); the union
        of their publishes is returned so an idempotency caller can difference
        the bot's complete workflow history — including workflows already in a
        terminal state — not just the currently-active one. Returns ``[]`` when
        the bot_uuid is unknown (the router turns that into a 404).

        Soft-deleted bot records are included: a successful DESTROY soft-deletes
        the bot, and its DESTROY workflow must stay visible so a crash-resumed
        destroy operation adopts it instead of re-issuing against a gone bot.
        """
        env = get_current_env()
        bot_records = self._bot_repo.list_by_bot_uuid_including_deleted(
            bot_uuid=bot_uuid, tenant=tenant, env=env
        )
        if not bot_records:
            return []

        summaries: list[BotPublishSummary] = []
        for bot_record in bot_records:
            for publish_record in self._publish_repo.list_by_bot_id(
                bot_id=bot_record.id, tenant=tenant, env=env
            ):
                summaries.append(
                    BotPublishSummary(
                        id=publish_record.id,
                        bot_id=publish_record.bot_id,
                        publish_type=publish_record.publish_type,
                        status=publish_record.status,
                        gmt_create=publish_record.gmt_create,
                    )
                )

        # Newest first by workflow id (monotonic), so adopt-by-query picks the
        # most recent matching workflow deterministically.
        summaries.sort(key=lambda s: s.id, reverse=True)
        return summaries

    # ====================================================================
    # EXECUTION ENGINE - BATCH PROCESSING AND DEVICE DRAIN
    # ====================================================================
    async def execute_stage(
        self,
        tenant: str,
        publish_id: int,
        operator: str,
        publish_record: PublishRecord | None = None,
        bot_record: BotRecord | None = None,
        current_stage: str | None = None,
        batches: list[PublishBatchRecord] | None = None,
    ) -> DrainResult:
        """
        Execute current stage batches with device drain and rolling update.

        Per D-03: Graceful drain with device status transitions
        Per D-04: Configurable drain timeout (default 30s)
        Per D-01: Batch processing with cooldown between batches

        Flow:
        1. Get publish and current stage info
        2. Get batches for current stage
        3. For each batch:
           a. For each device in batch: execute operation (update/restart/scale)
           b. Cooldown between batches
        4. On success: transition to next stage or SUCCESS

        Args:
            tenant: Tenant name for isolation
            publish_id: Publish ID to execute
            operator: User executing the stage
            publish_record: Pre-fetched publish record (skips DB query if provided)
            current_stage: Pre-fetched current stage (skips DB query if provided)
            batches: Pre-fetched pending batches (skips DB query if provided)

        Returns:
            DrainResult with execution status
        """
        env = get_current_env()
        logger.info(
            f"[execute_stage] publish_id={publish_id} operator={operator} "
            f"stage={current_stage} batches={len(batches) if batches else '?'} "
            f"publish_status={publish_record.status if publish_record else '?'}"
        )

        if publish_record is None:
            publish_record, bot_record = self._get_publish_and_bot_record(
                tenant, publish_id
            )
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        if publish_record.status != PublishStatus.ACTIVE.value:
            raise ValueError(
                f"Cannot execute stage in status '{publish_record.status}'. "
                f"Expected status: ACTIVE"
            )

        if current_stage is None or batches is None:
            current_stage, batches = self._get_pending_batches(tenant, publish_id)

        if not batches:
            logger.info(
                f"No pending batches for publish {publish_id}, stage={current_stage}"
            )
            return DrainResult(
                success=True,
                sessions_remaining=0,
                duration_seconds=0.0,
                timeout_reached=False,
            )

        if bot_record is None:
            bot_repo = self._bot_repo
            bot_record = bot_repo.get_by_id(
                publish_record.bot_id, tenant=tenant, env=env
            )

        batch_repo = self._publish_batch_repo

        # Get drain timeout from config (default 30s per D-04)
        publish_config = PublishConfig.model_validate(publish_record.extra_config or {})
        drain_timeout = publish_config.drain_timeout_seconds
        total_processed = 0
        total_failed = 0
        batch_start_time = datetime.now()

        for batch in batches:
            logger.info(
                f"Processing batch {batch.batch_index}: capacity={batch.batch_capacity}, "
                f"stage={batch.stage}"
            )

            # Mark batch as RUNNING when starting
            batch_repo.update_status(
                batch_id=batch.id,
                tenant=tenant,
                env=env,
                status=BatchStatus.RUNNING.value,
                modifier=operator,
            )

            batch_result = await self._execute_batch(
                tenant=tenant,
                publish_id=publish_id,
                batch=batch,
                publish_type=publish_record.publish_type,
                drain_timeout=drain_timeout,
                batch_repo=batch_repo,
                operator=operator,
                publish_record=publish_record,
                bot_record=bot_record,
            )
            total_processed += batch_result.processed_count
            total_failed += batch_result.failed_count

            # Check if any records in this batch are still awaiting callback
            record_repo = self._publish_record_repo
            counts = record_repo.count_records_by_batch_id(batch.id, tenant, env)
            has_pending_callbacks = counts.get("PROCESSING", 0) > 0

            if not has_pending_callbacks:
                # All records dispatched inline — mark batch COMPLETED/FAILED now.
                # BUT: the callback handler (running in a background thread) may
                # have already set the batch to FAILED.  If we overwrite FAILED
                # with COMPLETED here the publish will incorrectly auto-complete
                # to SUCCESS.  Check the current batch status first and preserve
                # a terminal status that was set by the callback handler.
                current_batch = batch_repo.get_by_id(batch.id, tenant, env)
                if current_batch and current_batch.status == BatchStatus.FAILED.value:
                    # Callback already set batch to FAILED — count the failures
                    # but do NOT overwrite the terminal status
                    failed_in_batch = counts.get("FAILED", 0)
                    total_failed += failed_in_batch
                    logger.info(
                        f"Batch {batch.id} already FAILED via callback "
                        f"({failed_in_batch} failed records), preserving callback result"
                    )
                elif current_batch and current_batch.status == BatchStatus.COMPLETED.value:
                    # Callback already set batch to COMPLETED — nothing to do
                    logger.info(
                        f"Batch {batch.id} already COMPLETED via callback, "
                        f"preserving callback result"
                    )
                else:
                    batch_status = (
                        BatchStatus.COMPLETED.value
                        if batch_result.success
                        else BatchStatus.FAILED.value
                    )
                    batch_repo.update_status(
                        batch_id=batch.id,
                        tenant=tenant,
                        env=env,
                        status=batch_status,
                        modifier=operator,
                    )
            else:
                # Async hooks dispatched — batch stays RUNNING until callbacks complete
                logger.info(
                    f"Batch {batch.id} has {counts.get('PROCESSING', 0)} async records, "
                    f"staying RUNNING until callbacks complete"
                )

            # Cooldown between batches (per D-01)
            if batch.cooldown_seconds > 0 and batch != batches[-1]:
                cooldown = batch.cooldown_seconds
                if is_paas_mock_mode():
                    cooldown = min(cooldown, 1)
                logger.info(f"Cooldown for {cooldown}s between batches")
                await asyncio.sleep(cooldown)

        duration = (datetime.now() - batch_start_time).total_seconds()

        logger.info(
            f"Stage execution dispatched: processed={total_processed}, "
            f"failed={total_failed}, duration={duration:.1f}s"
        )

        # Check if any batches are still RUNNING (awaiting async callbacks)
        all_batches = batch_repo.list_by_publish_id(publish_id, tenant, env)
        stage_batches = (
            [b for b in all_batches if b.stage == current_stage]
            if current_stage
            else all_batches
        )
        has_running_batches = any(
            b.status == BatchStatus.RUNNING.value for b in stage_batches
        )

        if has_running_batches:
            # Async hooks in flight — callback handler will drive stage advancement
            logger.info(
                f"Stage {current_stage} has running batches awaiting callbacks, "
                f"returning without advancing"
            )
            return DrainResult(
                success=True,
                sessions_remaining=0,
                duration_seconds=duration,
                timeout_reached=False,
            )

        # All batches completed inline (no async hooks) — advance state chain
        all_success = total_failed == 0

        if not all_success:
            # Stage failed - transition to FAILED
            repo = self._publish_repo
            repo.update_status(
                publish_id=publish_id,
                tenant=tenant,
                env=env,
                status=PublishStatus.FAILED.value,
                modifier=operator,
            )

            # D-01: Transition bot to FAILED status for CREATE publish failures
            from secbaas.community.api.device_manage import DeviceStatus

            if publish_record.publish_type == PublishType.CREATE.value:
                if bot_record and bot_record.status == BotStatus.PENDING.value:
                    bot_repo = self._bot_repo
                    logger.info(
                        f"[bot_failed] bot_id={bot_record.id} transitioning to FAILED "
                        f"due to CREATE publish failure publish_id={publish_id}"
                    )
                    bot_repo.update_status(
                        bot_id=bot_record.id,
                        tenant=tenant,
                        env=env,
                        status=BotStatus.FAILED.value,
                        modifier=operator,
                    )
            elif publish_record.publish_type == PublishType.STOP.value:
                if bot_record:
                    bot_repo = self._bot_repo
                    bot_repo.update_status(
                        bot_id=bot_record.id,
                        tenant=tenant,
                        env=env,
                        status=BotStatus.ACTIVE.value,
                        modifier=operator,
                    )
            elif publish_record.publish_type == PublishType.DESTROY.value:
                if bot_record:
                    bot_repo = self._bot_repo
                    logger.warning(
                        f"[destroy_failed_cleanup] bot_id={bot_record.id} "
                        f"releasing despite DESTROY publish failure publish_id={publish_id}"
                    )
                    bot_repo.complete_destroy(
                        bot_id=bot_record.id,
                        tenant=tenant,
                        env=env,
                        modifier=operator,
                    )
                    device_repo = self._device_repo
                    devices = device_repo.list_by_bot_id(
                        bot_id=bot_record.id, tenant=tenant, env=env
                    )
                    for device in devices:
                        try:
                            device_repo.update_status_by_device_uuid(
                                device_uuid=device.device_uuid,
                                tenant=tenant,
                                env=env,
                                status=DeviceStatus.RELEASED.value,
                            )
                            device_repo.soft_delete_by_device_uuid(
                                device_uuid=device.device_uuid,
                                tenant=tenant,
                                env=env,
                                modifier=operator,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[destroy_failed_cleanup] device={device.device_uuid} "
                                f"cleanup failed: {e}"
                            )
            # If UPDATE type, clean up orphaned PENDING clone to free UK slot
            elif publish_record.publish_type == PublishType.UPDATE.value:
                self._cleanup_pending_clone_on_update_failure(
                    tenant=tenant,
                    publish_id=publish_id,
                    operator=operator,
                )

            # Mark remaining PENDING records as FAILED (pre-created records in
            # stages/batches that never executed)
            try:
                from secbaas.community.api.publish_manage import PublishRecordResult

                all_batches = batch_repo.list_by_publish_id(publish_id, tenant, env)
                for b in all_batches:
                    pending = record_repo.list_by_publish_id_and_batch_id(
                        publish_id,
                        b.id,
                        tenant,
                        env,
                        status=PublishRecordResult.PENDING.value,
                    )
                    for rec in pending:
                        record_repo.update_result(
                            record_id=rec.id,
                            tenant=tenant,
                            env=env,
                            result_status=PublishRecordResult.FAILED.value,
                            result_message=(
                                "Publish marked as FAILED before this record "
                                "could be processed"
                            ),
                            modifier=operator,
                        )
            except Exception as cleanup_err:
                logger.warning(
                    f"[execute_stage] Failed to cleanup PENDING records "
                    f"for publish_id={publish_id}: {cleanup_err}"
                )

            return DrainResult(
                success=False,
                sessions_remaining=0,
                duration_seconds=duration,
                timeout_reached=False,
            )

        # Check if there are more stages after this one
        next_stage, next_batches = self._get_pending_batches(tenant, publish_id)

        if next_batches:
            # More stages exist - always pause for stage gate approval
            logger.info(
                f"[stage_gate] publish_id={publish_id} stage={current_stage} "
                f"complete, transitioning to APPROVING"
            )
            repo = self._publish_repo
            repo.update_status(
                publish_id=publish_id,
                tenant=tenant,
                env=env,
                status=PublishStatus.APPROVING.value,
                modifier=operator,
            )
        else:
            # All stages complete - check auto-complete
            if publish_config.auto_complete:
                logger.info(
                    f"[auto_complete] publish_id={publish_id} auto-completing: "
                    f"all stages succeeded and auto_complete=True"
                )
                await self.complete_publish(
                    tenant=tenant,
                    publish_id=publish_id,
                    operator=operator,
                    publish_record=publish_record,
                    bot_record=bot_record,
                )

        return DrainResult(
            success=all_success,
            sessions_remaining=0,
            duration_seconds=duration,
            timeout_reached=False,
        )

    async def _execute_batch(
        self,
        tenant: str,
        publish_id: int,
        batch: Any,
        publish_type: str,
        drain_timeout: int,
        batch_repo: Any,
        operator: str,
        publish_record: PublishRecord | None = None,
        bot_record: BotRecord | None = None,
    ) -> BatchResult:
        """Execute a single batch: process each device with appropriate operation.

        Device Operation Flows by PublishType:
        ┌──────────────┬────────────────────────────────────────────────────────────────┐
        │ PublishType  │ Device Operations                                              │
        ├──────────────┼────────────────────────────────────────────────────────────────┤
        │ CREATE       │ Query PENDING devices → start each → record CREATE event       │
        │ UPDATE       │ ACTIVE → UPDATING → drain → destroy → create new → start       │
        │ UPDATE_DEVICE│ ACTIVE → UPDATING → drain → destroy → create new → start       │
        │ RESTART      │ ACTIVE → UPDATING → drain → restart → record RESTART event     │
        │ SCALE_UP     │ Create new devices from template → start each → CREATE event   │
        │ SCALE_DOWN   │ ACTIVE → UPDATING → destroy oldest → record DESTROY event      │
        │ DESTROY      │ ACTIVE → UPDATING → drain → destroy → DESTROY event → cleanup  │
        └──────────────┴────────────────────────────────────────────────────────────────┘

        Device Status Transitions (Per D-08):
        ┌──────────────┬────────────────────────────────────────────────────────────────┐
        │ Operation    │ Device Status Transitions                                     │
        ├──────────────┼────────────────────────────────────────────────────────────────┤
        │ CREATE       │ PENDING → ACTIVE (via DeviceService.start())                    │
        │ UPDATE       │ ACTIVE → UPDATING → (destroyed) → new device PENDING → ACTIVE  │
        │ UPDATE_DEVICE│ ACTIVE → UPDATING → (destroyed) → new device PENDING → ACTIVE  │
        │ RESTART      │ ACTIVE → UPDATING → ACTIVE (after restart)                     │
        │ SCALE_UP     │ New device PENDING → ACTIVE (create + start)                   │
        │ SCALE_DOWN   │ ACTIVE → UPDATING → RELEASED (destroy)                         │
        │ DESTROY      │ ACTIVE → UPDATING → RELEASED (destroy) + bot-device rel delete │
        └──────────────┴────────────────────────────────────────────────────────────────┘

        Batch Status Transitions:
        - PENDING → RUNNING (when batch execution starts)
        - RUNNING → COMPLETED (all devices processed successfully)
        - RUNNING → FAILED (any device operation fails)

        Per D-08: Device status transitions ACTIVE→UPDATING→TERMINATING

        Args:
            tenant: Tenant name for isolation
            publish_id: Publish operation ID
            batch: Batch to execute (contains batch_capacity, stage info)
            publish_type: Type of publish operation
            drain_timeout: Seconds to wait for session drain
            batch_repo: Repository for batch status updates
            modifier: User ID for audit trail

        Returns:
            BatchResult with success status, processed/failed counts, and error message
        """
        logger.info(
            f"Executing batch {batch.batch_index} with {batch.batch_capacity} devices"
        )

        # Determine operation type
        if publish_type == PublishType.CREATE.value:
            return await self._execute_create_batch(
                tenant,
                publish_id,
                batch,
                operator,
                publish_record=publish_record,
                bot_record=bot_record,
            )
        elif publish_type == PublishType.UPDATE.value:
            return await self._execute_update_batch(
                tenant,
                publish_id,
                batch,
                drain_timeout,
                operator,
                publish_record=publish_record,
                bot_record=bot_record,
            )
        elif publish_type == PublishType.RESTART.value:
            return await self._execute_restart_batch(
                tenant,
                publish_id,
                batch,
                drain_timeout,
                operator,
                publish_record=publish_record,
                bot_record=bot_record,
            )
        elif publish_type in (PublishType.SCALE_UP.value, PublishType.SCALE_DOWN.value):
            return await self._execute_scale_batch(
                tenant,
                publish_id,
                batch,
                publish_type,
                operator,
                publish_record=publish_record,
                bot_record=bot_record,
            )
        elif publish_type == PublishType.DESTROY.value:
            return await self._execute_destroy_batch(
                tenant,
                publish_id,
                batch,
                drain_timeout,
                operator,
                publish_record=publish_record,
                bot_record=bot_record,
            )
        elif publish_type == PublishType.STOP.value:
            return await self._execute_stop_batch(
                tenant,
                publish_id,
                batch,
                drain_timeout,
                operator,
                publish_record=publish_record,
                bot_record=bot_record,
            )
        elif publish_type == PublishType.UPDATE_DEVICE.value:
            # UPDATE_DEVICE reuses _execute_update_batch device-level logic
            # (drain → destroy → create new → start). target_bot_id is not set,
            # so _execute_update_batch falls back to current bot record config.
            return await self._execute_update_batch(
                tenant,
                publish_id,
                batch,
                drain_timeout,
                operator,
                publish_record=publish_record,
                bot_record=bot_record,
            )
        else:
            return BatchResult(
                success=False,
                processed_count=0,
                failed_count=batch.batch_capacity,
                error_message=f"Unknown publish type: {publish_type}",
            )

    async def _execute_create_batch(
        self,
        tenant: str,
        publish_id: int,
        batch: Any,
        operator: str,
        publish_record: PublishRecord | None = None,
        bot_record: BotRecord | None = None,
    ) -> BatchResult:
        """Execute CREATE batch: start existing PENDING devices for the bot."""
        env = get_current_env()
        from secbaas.community.api.device_manage import DeviceStatus

        logger.info(
            f"[create_batch] publish_id={publish_id} batch={batch.batch_index}/{batch.batch_capacity}"
        )

        if publish_record is None:
            publish_repo = self._publish_repo
            publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
        if publish_record is None:
            raise PublishNotFoundError(publish_id)
        if bot_record is None:
            bot_repo = self._bot_repo
            bot_record = bot_repo.get_by_id(
                publish_record.bot_id, tenant=tenant, env=env
            )

        if not bot_record:
            logger.error(f"Bot not found: {publish_record.bot_id}")
            return BatchResult(
                success=False,
                processed_count=0,
                failed_count=batch.batch_capacity,
                error_message=f"Bot not found: {publish_record.bot_id}",
            )

        device_repo = self._device_repo
        record_repo = self._publish_record_repo

        pending_records = record_repo.list_by_publish_id_and_batch_id(
            publish_id, batch.id, tenant, env, status=PublishRecordResult.PENDING.value
        )

        device_ids = [r.device_id for r in pending_records if r.device_id]
        device_map = (
            device_repo.get_by_ids(device_ids, tenant, env) if device_ids else {}
        )

        logger.info(
            f"[create_batch] publish_id={publish_id} batch={batch.batch_index} "
            f"pending_records={len(pending_records)}"
        )

        processed = 0
        failed = 0

        for record in pending_records:
            device = device_map.get(record.device_id) if record.device_id else None
            if not device:
                logger.warning(
                    f"Skipping record {record.id}: device not found "
                    f"(device_id={record.device_id})"
                )
                continue

            record_repo.update_result(
                record_id=record.id,
                tenant=tenant,
                env=env,
                result_status=PublishRecordResult.PROCESSING.value,
                modifier=operator,
            )
            try:
                logger.info(
                    f"[start_batch] Calling start_device for device "
                    f"id={device.id} uuid={device.device_uuid}"
                )
                result = await self._device_service.start_device(
                    tenant=tenant,
                    device_uuid=device.device_uuid,
                    modifier=operator,
                    publish_id=publish_id,
                )
                logger.info(
                    f"[start_batch] start_device result: type={type(result).__name__}, "
                    f"status={getattr(result, 'status', 'N/A')}"
                )

                if result.status == DeviceStatus.FAILED.value:
                    failed += 1
                    logger.error(
                        f"Device {device.device_uuid} start failed: {result.err_msg}"
                    )
                    record_repo.update_result(
                        record_id=record.id,
                        tenant=tenant,
                        env=env,
                        result_status=PublishRecordResult.FAILED.value,
                        result_message=(result.err_msg or "Device start failed")[:4000],
                        modifier=operator,
                    )
                elif result.status == DeviceStatus.ACTIVE.value:
                    processed += 1
                    record_repo.update_result(
                        record_id=record.id,
                        tenant=tenant,
                        env=env,
                        result_status=PublishRecordResult.SUCCESS.value,
                        result_message="Device started successfully",
                        modifier=operator,
                    )
                else:
                    logger.info(
                        f"Device {device.device_uuid} start hook dispatched async, "
                        f"awaiting callback"
                    )

            except Exception as e:
                failed += 1
                logger.error(f"Failed to start device {device.id}: {e}")
                record_repo.update_result(
                    record_id=record.id,
                    tenant=tenant,
                    env=env,
                    result_status=PublishRecordResult.FAILED.value,
                    result_message=str(e)[:4000],
                    modifier=operator,
                )

        logger.info(
            f"[create_batch] batch={batch.batch_index} complete: "
            f"processed={processed} failed={failed} publish_id={publish_id}"
        )
        return BatchResult(
            success=failed == 0,
            processed_count=processed,
            failed_count=failed,
            error_message=None if failed == 0 else f"{failed} devices failed",
        )

    async def _execute_update_batch(
        self,
        tenant: str,
        publish_id: int,
        batch: Any,
        drain_timeout: int,
        operator: str,
        publish_record: PublishRecord | None = None,
        bot_record: BotRecord | None = None,
    ) -> BatchResult:
        """
        Execute UPDATE batch: drain, update device config, restart in-place.

        Per D-03: Graceful drain with UPDATING status.
        Devices reuse existing records — config is updated to the new BotConfig
        from the target bot record, then restarted via DeviceService.restart_device().
        Bot-device relationships remain intact during execution; transfer happens
        at complete_publish.
        """
        env = get_current_env()
        from secbaas.community.api.bot_manage import BotConfig
        from secbaas.community.api.device_manage import DeviceConfig, DeviceStatus

        logger.info(f"Executing UPDATE batch {batch.batch_index}")

        device_repo = self._device_repo
        if publish_record is None:
            publish_repo = self._publish_repo
            publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        if bot_record is None:
            bot_repo = self._bot_repo
            bot_record = bot_repo.get_by_id(
                publish_record.bot_id, tenant=tenant, env=env
            )

        if not bot_record:
            logger.error(f"Bot not found: {publish_record.bot_id}")
            return BatchResult(
                success=False,
                processed_count=0,
                failed_count=batch.batch_capacity,
                error_message=f"Bot not found: {publish_record.bot_id}",
            )

        # Get target bot record (new PENDING bot created during create_publish)
        target_bot_id = (publish_record.extra_config or {}).get("target_bot_id")
        if target_bot_id:
            bot_repo = self._bot_repo
            target_bot_record = bot_repo.get_by_id(
                target_bot_id, tenant=tenant, env=env
            )
        else:
            target_bot_record = bot_record
            logger.warning(
                "No target_bot_id in publish extra_config, "
                "using current bot record for config"
            )

        # Derive new BotConfig from target bot record
        new_bot_config = (
            BotConfig.model_validate(target_bot_record.extra_config)
            if target_bot_record and target_bot_record.extra_config
            else BotConfig()
        )

        record_repo = self._publish_record_repo

        pending_records = record_repo.list_by_publish_id_and_batch_id(
            publish_id, batch.id, tenant, env, status=PublishRecordResult.PENDING.value
        )

        if not pending_records:
            logger.warning(
                f"[update_batch] NO_PENDING_RECORDS: batch={batch.batch_index} "
                f"publish_id={publish_record.id} bot_id={publish_record.bot_id}"
            )
            return BatchResult(success=True, processed_count=0, failed_count=0)

        device_ids = [r.device_id for r in pending_records if r.device_id]
        device_map = (
            device_repo.get_by_ids(device_ids, tenant, env) if device_ids else {}
        )

        processed = 0
        failed = 0

        for record in pending_records:
            device_record = (
                device_map.get(record.device_id) if record.device_id else None
            )
            if not device_record:
                logger.warning(
                    f"Skipping record {record.id}: device not found "
                    f"(device_id={record.device_id})"
                )
                continue

            record_repo.update_result(
                record_id=record.id,
                tenant=tenant,
                env=env,
                result_status=PublishRecordResult.PROCESSING.value,
                modifier=operator,
            )
            try:
                logger.info(
                    f"Setting device {device_record.id} (uuid={device_record.device_uuid}, "
                    f"publish_id={publish_record.id}) to UPDATING"
                )
                device_repo.update_status_by_device_uuid(
                    device_uuid=device_record.device_uuid,
                    tenant=tenant,
                    env=env,
                    status=DeviceStatus.UPDATING.value,
                )

                new_device_extra_config = DeviceConfig(
                    template_uuid=target_bot_record.template_uuid
                    if target_bot_record
                    else bot_record.template_uuid,
                    deploy_config=new_bot_config.deploy_config,
                )
                device_repo.update_device(
                    device_id=device_record.id,
                    tenant=tenant,
                    env=env,
                    extra_config=new_device_extra_config.model_dump(exclude_none=True),
                    modifier=operator,
                )
                logger.info(
                    f"Device {device_record.id} config updated to new BotConfig"
                )

                drain_result = await self._drain_device(
                    tenant=tenant,
                    device_id=device_record.id,
                    timeout_seconds=drain_timeout,
                )

                if not drain_result.success:
                    logger.warning(
                        f"Device {device_record.id} drain timed out with "
                        f"{drain_result.sessions_remaining} sessions remaining"
                    )

                logger.info(f"Updating device {device_record.device_uuid} in-place")
                update_result = await self._device_service.update_device(
                    tenant=tenant,
                    device_uuid=device_record.device_uuid,
                    modifier=operator,
                    publish_id=publish_id,
                )

                if update_result.status == DeviceStatus.FAILED.value:
                    raise ValueError(
                        f"Device update failed for {device_record.device_uuid}: "
                        f"{update_result.err_msg or 'unknown error'}"
                    )
                elif update_result.status == DeviceStatus.ACTIVE.value:
                    processed += 1
                    record_repo.update_result(
                        record_id=record.id,
                        tenant=tenant,
                        env=env,
                        result_status=PublishRecordResult.SUCCESS.value,
                        result_message="Device updated successfully",
                        modifier=operator,
                    )
                else:
                    logger.info(
                        f"Device {device_record.device_uuid} update hook dispatched async"
                    )

            except Exception as e:
                failed += 1
                logger.error(f"Failed to update device {device_record.id}: {e}")

                try:
                    device_repo.update_status_by_device_uuid(
                        device_uuid=device_record.device_uuid,
                        tenant=tenant,
                        env=env,
                        status=DeviceStatus.FAILED.value,
                    )
                except Exception as rollback_err:
                    logger.error(
                        f"Failed to roll back status for device {device_record.id}: {rollback_err}"
                    )

                record_repo.update_result(
                    record_id=record.id,
                    tenant=tenant,
                    env=env,
                    result_status=PublishRecordResult.FAILED.value,
                    result_message=str(e)[:4000],
                    modifier=operator,
                )

        logger.info(
            f"[update_batch] batch={batch.batch_index} complete: "
            f"processed={processed} failed={failed} publish_id={publish_id}"
        )
        return BatchResult(
            success=failed == 0,
            processed_count=processed,
            failed_count=failed,
            error_message=None if failed == 0 else f"{failed} devices failed",
        )

    async def _execute_restart_batch(
        self,
        tenant: str,
        publish_id: int,
        batch: Any,
        drain_timeout: int,
        operator: str,
        publish_record: PublishRecord | None = None,
        bot_record: BotRecord | None = None,
    ) -> BatchResult:
        """Execute RESTART batch: drain and restart each device."""
        env = get_current_env()
        from secbaas.community.api.device_manage import DeviceStatus

        logger.info(
            f"[restart_batch] publish_id={publish_id} batch={batch.batch_index}/{batch.batch_capacity}"
        )

        device_repo = self._device_repo
        if publish_record is None:
            publish_repo = self._publish_repo
            publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        record_repo = self._publish_record_repo

        pending_records = record_repo.list_by_publish_id_and_batch_id(
            publish_id, batch.id, tenant, env, status=PublishRecordResult.PENDING.value
        )

        if not pending_records:
            logger.warning(
                f"[restart_batch] NO_PENDING_RECORDS: batch={batch.batch_index} "
                f"publish_id={publish_id} bot_id={publish_record.bot_id}"
            )
            return BatchResult(
                success=False,
                processed_count=0,
                failed_count=0,
                error_message=f"No pending records found for batch {batch.batch_index}",
            )

        device_ids = [r.device_id for r in pending_records if r.device_id]
        device_map = (
            device_repo.get_by_ids(device_ids, tenant, env) if device_ids else {}
        )

        processed = 0
        failed = 0

        for record in pending_records:
            device_record = (
                device_map.get(record.device_id) if record.device_id else None
            )
            if not device_record:
                logger.warning(
                    f"Skipping record {record.id}: device not found "
                    f"(device_id={record.device_id})"
                )
                continue

            record_repo.update_result(
                record_id=record.id,
                tenant=tenant,
                env=env,
                result_status=PublishRecordResult.PROCESSING.value,
                modifier=operator,
            )
            try:
                device_repo.update_status_by_device_uuid(
                    device_uuid=device_record.device_uuid,
                    tenant=tenant,
                    env=env,
                    status=DeviceStatus.UPDATING.value,
                )

                await self._drain_device(
                    tenant=tenant,
                    device_id=device_record.id,
                    timeout_seconds=drain_timeout,
                )

                restart_result = await self._device_service.restart_device(
                    tenant=tenant,
                    device_uuid=device_record.device_uuid,
                    modifier=operator,
                    publish_id=publish_id,
                )

                if restart_result.status == DeviceStatus.FAILED.value:
                    raise ValueError(
                        f"Device restart failed for {device_record.device_uuid}: "
                        f"{restart_result.err_msg or 'unknown error'}"
                    )
                elif restart_result.status == DeviceStatus.ACTIVE.value:
                    processed += 1
                    record_repo.update_result(
                        record_id=record.id,
                        tenant=tenant,
                        env=env,
                        result_status=PublishRecordResult.SUCCESS.value,
                        result_message="Device restarted successfully",
                        modifier=operator,
                    )
                else:
                    logger.info(
                        f"Device {device_record.device_uuid} restart hook dispatched async"
                    )

            except Exception as e:
                failed += 1
                logger.error(f"Failed to restart device {device_record.id}: {e}")

                try:
                    device_repo.update_status_by_device_uuid(
                        device_uuid=device_record.device_uuid,
                        tenant=tenant,
                        env=env,
                        status=DeviceStatus.FAILED.value,
                    )
                except Exception as rollback_err:
                    logger.error(
                        f"Failed to roll back status for device {device_record.id}: {rollback_err}"
                    )

                record_repo.update_result(
                    record_id=record.id,
                    tenant=tenant,
                    env=env,
                    result_status=PublishRecordResult.FAILED.value,
                    result_message=str(e)[:4000],
                    modifier=operator,
                )

        logger.info(
            f"[restart_batch] batch={batch.batch_index} complete: "
            f"processed={processed} failed={failed} publish_id={publish_id}"
        )
        return BatchResult(
            success=failed == 0, processed_count=processed, failed_count=failed
        )

    async def _execute_scale_batch(
        self,
        tenant: str,
        publish_id: int,
        batch: Any,
        publish_type: str,
        operator: str,
        publish_record: PublishRecord | None = None,
        bot_record: BotRecord | None = None,
    ) -> BatchResult:
        """Execute SCALE batch: SCALE_UP (create) or SCALE_DOWN (destroy)."""
        env = get_current_env()
        from secbaas.community.api.device_manage import DeviceStatus

        logger.info(f"Executing SCALE batch {batch.batch_index}: type={publish_type}")

        device_repo = self._device_repo
        if publish_record is None:
            publish_repo = self._publish_repo
            publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        if bot_record is None:
            bot_repo = self._bot_repo
            bot_record = bot_repo.get_by_id(
                publish_record.bot_id, tenant=tenant, env=env
            )
        if not bot_record:
            logger.error(f"Bot not found: {publish_record.bot_id}")
            return BatchResult(
                success=False,
                processed_count=0,
                failed_count=batch.batch_capacity,
                error_message=f"Bot not found: {publish_record.bot_id}",
            )

        if publish_type == PublishType.SCALE_UP.value:
            # Need template for device creation
            if not bot_record.template_uuid:
                logger.error(f"Bot has no template_uuid: {bot_record.id}")
                return BatchResult(
                    success=False,
                    processed_count=0,
                    failed_count=batch.batch_capacity,
                    error_message=f"Bot has no template_uuid: {bot_record.id}",
                )

            template = self._template_service.get_online_template_by_uuid(
                tenant, bot_record.template_uuid
            )
            if not template:
                logger.error(f"Template not found: {bot_record.template_uuid}")
                return BatchResult(
                    success=False,
                    processed_count=0,
                    failed_count=batch.batch_capacity,
                    error_message=f"Template not found: {bot_record.template_uuid}",
                )

        record_repo = self._publish_record_repo

        pending_records = record_repo.list_by_publish_id_and_batch_id(
            publish_id, batch.id, tenant, env, status=PublishRecordResult.PENDING.value
        )

        if not pending_records:
            logger.warning(
                f"[scale_batch] NO_PENDING_RECORDS: batch={batch.batch_index} "
                f"publish_id={publish_id} bot_id={publish_record.bot_id} "
                f"type={publish_type}"
            )
            return BatchResult(
                success=False,
                processed_count=0,
                failed_count=0,
                error_message=f"No pending records found for batch {batch.batch_index}",
            )

        device_ids = [r.device_id for r in pending_records if r.device_id]
        device_map = (
            device_repo.get_by_ids(device_ids, tenant, env) if device_ids else {}
        )

        processed = 0
        failed = 0

        if publish_type == PublishType.SCALE_UP.value:
            for record in pending_records:
                device = device_map.get(record.device_id) if record.device_id else None
                if not device:
                    logger.warning(
                        f"Skipping record {record.id}: device not found "
                        f"(device_id={record.device_id})"
                    )
                    continue

                record_repo.update_result(
                    record_id=record.id,
                    tenant=tenant,
                    env=env,
                    result_status=PublishRecordResult.PROCESSING.value,
                    modifier=operator,
                )

                try:
                    result = await self._device_service.start_device(
                        tenant=tenant,
                        device_uuid=device.device_uuid,
                        modifier=operator,
                        publish_id=publish_id,
                    )

                    if result.status == DeviceStatus.FAILED.value:
                        failed += 1
                        logger.error(
                            f"Device {device.device_uuid} start failed: "
                            f"{result.err_msg}"
                        )
                        record_repo.update_result(
                            record_id=record.id,
                            tenant=tenant,
                            env=env,
                            result_status=PublishRecordResult.FAILED.value,
                            result_message=(result.err_msg or "Device start failed")[
                                :4000
                            ],
                            modifier=operator,
                        )
                    elif result.status == DeviceStatus.PENDING.value:
                        logger.info(
                            f"Device {device.device_uuid} start hook dispatched async"
                        )
                    else:
                        processed += 1
                        record_repo.update_result(
                            record_id=record.id,
                            tenant=tenant,
                            env=env,
                            result_status=PublishRecordResult.SUCCESS.value,
                            result_message="Device scaled up successfully",
                            modifier=operator,
                        )

                except Exception as e:
                    failed += 1
                    logger.error(f"Failed to start device for scale up: {e}")
                    record_repo.update_result(
                        record_id=record.id,
                        tenant=tenant,
                        env=env,
                        result_status=PublishRecordResult.FAILED.value,
                        result_message=str(e)[:4000],
                        modifier=operator,
                    )

        else:  # SCALE_DOWN
            for record in pending_records:
                device_record = (
                    device_map.get(record.device_id) if record.device_id else None
                )
                if not device_record:
                    logger.warning(
                        f"Skipping record {record.id}: device not found "
                        f"(device_id={record.device_id})"
                    )
                    continue

                record_repo.update_result(
                    record_id=record.id,
                    tenant=tenant,
                    env=env,
                    result_status=PublishRecordResult.PROCESSING.value,
                    modifier=operator,
                )
                try:
                    device_repo.update_status_by_device_uuid(
                        device_uuid=device_record.device_uuid,
                        tenant=tenant,
                        env=env,
                        status=DeviceStatus.UPDATING.value,
                    )

                    logger.info(
                        f"[scale_batch] Calling destroy_device_by_uuid for device "
                        f"id={device_record.id} uuid={device_record.device_uuid} "
                        f"provider_id={device_record.provider_device_id}"
                    )
                    destroy_response = (
                        await self._device_service.destroy_device_by_uuid(
                            tenant=tenant,
                            device_uuid=device_record.device_uuid,
                            modifier=operator,
                        )
                    )

                    if destroy_response.success:
                        processed += 1

                        rel_repo = self._rel_repo
                        rel_records = rel_repo.list_by_bot_id(
                            bot_id=publish_record.bot_id,
                            tenant=tenant,
                            env=env,
                        )
                        for rel in rel_records:
                            if rel.device_uuid == device_record.device_uuid:
                                rel_repo.soft_delete(
                                    rel_id=rel.id,
                                    tenant=tenant,
                                    env=env,
                                    modifier=operator,
                                )

                        if destroy_response.error_message:
                            logger.warning(
                                f"Device destroyed with warnings: {destroy_response.error_message}"
                            )

                        hook_msg = None
                        if destroy_response.hook_result:
                            hook_msg = serialize_hook_result(
                                exit_code=destroy_response.hook_result.exit_code,
                                stdout=destroy_response.hook_result.stdout,
                                stderr=destroy_response.hook_result.stderr,
                                message="Device scaled down successfully",
                            )
                        record_repo.update_result(
                            record_id=record.id,
                            tenant=tenant,
                            env=env,
                            result_status=PublishRecordResult.SUCCESS.value,
                            result_message=hook_msg
                            or "Device scaled down successfully",
                            modifier=operator,
                        )
                    else:
                        failed += 1
                        error_msg = (
                            destroy_response.error_message
                            or "Unknown destruction error"
                        )
                        hook_msg = None
                        if destroy_response.hook_result:
                            hook_msg = serialize_hook_result(
                                exit_code=destroy_response.hook_result.exit_code,
                                stdout=destroy_response.hook_result.stdout,
                                stderr=destroy_response.hook_result.stderr,
                                message=f"Device destruction failed: {error_msg}",
                            )
                        record_repo.update_result(
                            record_id=record.id,
                            tenant=tenant,
                            env=env,
                            result_status=PublishRecordResult.FAILED.value,
                            result_message=hook_msg
                            or f"Device destruction failed: {error_msg}",
                            modifier=operator,
                        )

                except Exception as e:
                    failed += 1
                    logger.error(f"Failed to destroy device {device_record.id}: {e}")
                    record_repo.update_result(
                        record_id=record.id,
                        tenant=tenant,
                        env=env,
                        result_status=PublishRecordResult.FAILED.value,
                        result_message=str(e)[:4000],
                        modifier=operator,
                    )

        logger.info(
            f"[scale_batch] batch={batch.batch_index} complete: "
            f"type={publish_type} processed={processed} failed={failed} "
            f"publish_id={publish_id}"
        )
        return BatchResult(
            success=failed == 0, processed_count=processed, failed_count=failed
        )

    async def _execute_destroy_batch(
        self,
        tenant: str,
        publish_id: int,
        batch: Any,
        drain_timeout: int,
        operator: str,
        publish_record: PublishRecord | None = None,
        bot_record: BotRecord | None = None,
    ) -> BatchResult:
        """Execute DESTROY batch: drain and destroy devices, cleanup after all batches.

        Per D-03: Standard graceful drain with UPDATING status
        Per D-04: Soft delete bot and devices after destruction
        Per D-06: Record DESTROY events for each device
        """
        env = get_current_env()
        from secbaas.community.api.device_manage import DeviceStatus

        logger.info(
            f"[destroy_batch] publish_id={publish_id} batch={batch.batch_index}/{batch.batch_capacity}"
        )

        device_repo = self._device_repo
        rel_repo = self._rel_repo
        if publish_record is None:
            publish_repo = self._publish_repo
            publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        record_repo = self._publish_record_repo

        pending_records = record_repo.list_by_publish_id_and_batch_id(
            publish_id, batch.id, tenant, env, status=PublishRecordResult.PENDING.value
        )

        if not pending_records:
            logger.warning(
                f"[destroy_batch] NO_PENDING_RECORDS: batch={batch.batch_index} "
                f"publish_id={publish_id} bot_id={publish_record.bot_id}"
            )
            return BatchResult(
                success=True,
                processed_count=0,
                failed_count=0,
            )

        device_ids = [r.device_id for r in pending_records if r.device_id]
        device_map = (
            device_repo.get_by_ids(device_ids, tenant, env) if device_ids else {}
        )

        processed = 0
        failed = 0

        for record in pending_records:
            device_record = (
                device_map.get(record.device_id) if record.device_id else None
            )
            if not device_record:
                logger.warning(
                    f"Skipping record {record.id}: device not found "
                    f"(device_id={record.device_id})"
                )
                continue

            record_repo.update_result(
                record_id=record.id,
                tenant=tenant,
                env=env,
                result_status=PublishRecordResult.PROCESSING.value,
                modifier=operator,
            )
            try:
                # Step 1: Set UPDATING status (per D-03)
                logger.info(f"Setting device {device_record.id} to UPDATING")
                device_repo.update_status_by_device_uuid(
                    device_uuid=device_record.device_uuid,
                    tenant=tenant,
                    env=env,
                    status=DeviceStatus.UPDATING.value,
                )

                # Step 2: Drain sessions (per D-03)
                drain_result = await self._drain_device(
                    tenant=tenant,
                    device_id=device_record.id,
                    timeout_seconds=drain_timeout,
                )

                if not drain_result.success:
                    logger.warning(
                        f"Device {device_record.id} drain timed out with "
                        f"{drain_result.sessions_remaining} sessions remaining"
                    )

                # Step 3: Destroy device (per D-04, DeviceService handles soft delete)
                logger.info(
                    f"[destroy_batch] Destroying device id={device_record.id} "
                    f"uuid={device_record.device_uuid} "
                    f"provider_id={device_record.provider_device_id}"
                )
                destroy_response = await self._device_service.destroy_device_by_uuid(
                    tenant=tenant,
                    device_uuid=device_record.device_uuid,
                    modifier=operator,
                )

                if not destroy_response.success:
                    error_msg = (
                        destroy_response.error_message or "Unknown destruction error"
                    )
                    logger.error(
                        f"Device {device_record.id} destruction failed: {error_msg}"
                    )
                    failed += 1

                    # Phase 2b: Update record to FAILED
                    hook_msg = None
                    if destroy_response.hook_result:
                        hook_msg = serialize_hook_result(
                            exit_code=destroy_response.hook_result.exit_code,
                            stdout=destroy_response.hook_result.stdout,
                            stderr=destroy_response.hook_result.stderr,
                            message=f"Device destruction failed: {error_msg}",
                        )
                    record_repo.update_result(
                        record_id=record.id,
                        tenant=tenant,
                        env=env,
                        result_status=PublishRecordResult.FAILED.value,
                        result_message=hook_msg
                        or f"Device destruction failed: {error_msg}",
                        modifier=operator,
                    )
                    continue

                if destroy_response.error_message:
                    logger.warning(
                        f"Device {device_record.id} destroyed with warnings: {destroy_response.error_message}"
                    )

                processed += 1

                rel_records = rel_repo.list_by_bot_id(
                    bot_id=publish_record.bot_id, tenant=tenant, env=env
                )
                for rel in rel_records:
                    if rel.device_uuid == device_record.device_uuid:
                        rel_repo.soft_delete(
                            rel_id=rel.id, tenant=tenant, env=env, modifier=operator
                        )
                        logger.info(
                            f"Soft-deleted relationship {rel.id} for device {device_record.id}"
                        )
                        break

                hook_msg = None
                if destroy_response.hook_result:
                    hook_msg = serialize_hook_result(
                        exit_code=destroy_response.hook_result.exit_code,
                        stdout=destroy_response.hook_result.stdout,
                        stderr=destroy_response.hook_result.stderr,
                        message="Device destroyed successfully",
                    )
                record_repo.update_result(
                    record_id=record.id,
                    tenant=tenant,
                    env=env,
                    result_status=PublishRecordResult.SUCCESS.value,
                    result_message=hook_msg or "Device destroyed successfully",
                    modifier=operator,
                )

            except Exception as e:
                failed += 1
                logger.error(f"Failed to destroy device {device_record.id}: {e}")

                record_repo.update_result(
                    record_id=record.id,
                    tenant=tenant,
                    env=env,
                    result_status=PublishRecordResult.FAILED.value,
                    result_message=str(e)[:4000],
                    modifier=operator,
                )

        logger.info(
            f"[destroy_batch] batch={batch.batch_index} complete: "
            f"processed={processed} failed={failed} publish_id={publish_id}"
        )
        return BatchResult(
            success=failed == 0,
            processed_count=processed,
            failed_count=failed,
            error_message=None
            if failed == 0
            else f"{failed} devices failed to destroy",
        )

    async def _execute_stop_batch(
        self,
        tenant: str,
        publish_id: int,
        batch: Any,
        drain_timeout: int,
        operator: str,
        publish_record: PublishRecord | None = None,
        bot_record: BotRecord | None = None,
    ) -> BatchResult:
        env = get_current_env()

        logger.info(
            f"[stop_batch] publish_id={publish_id} batch={batch.batch_index}/{batch.batch_capacity}"
        )

        device_repo = self._device_repo
        if publish_record is None:
            publish_repo = self._publish_repo
            publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        record_repo = self._publish_record_repo

        pending_records = record_repo.list_by_publish_id_and_batch_id(
            publish_id, batch.id, tenant, env, status=PublishRecordResult.PENDING.value
        )

        if not pending_records:
            logger.warning(
                f"[stop_batch] NO_PENDING_RECORDS: batch={batch.batch_index} "
                f"publish_id={publish_id} bot_id={publish_record.bot_id}"
            )
            return BatchResult(
                success=True,
                processed_count=0,
                failed_count=0,
            )

        device_ids = [r.device_id for r in pending_records if r.device_id]
        device_map = (
            device_repo.get_by_ids(device_ids, tenant, env) if device_ids else {}
        )

        processed = 0
        failed = 0

        for record in pending_records:
            device_record = (
                device_map.get(record.device_id) if record.device_id else None
            )
            if not device_record:
                logger.warning(
                    f"Skipping record {record.id}: device not found "
                    f"(device_id={record.device_id})"
                )
                continue

            record_repo.update_result(
                record_id=record.id,
                tenant=tenant,
                env=env,
                result_status=PublishRecordResult.PROCESSING.value,
                modifier=operator,
            )
            try:
                stop_response = await self._device_service.stop_device_by_uuid(
                    tenant=tenant,
                    device_uuid=device_record.device_uuid,
                    modifier=operator,
                )

                if not stop_response.success:
                    error_msg = stop_response.error_message or "Unknown stop error"
                    logger.error(f"Device {device_record.id} stop failed: {error_msg}")
                    failed += 1

                    hook_msg = None
                    if stop_response.hook_result:
                        hook_msg = serialize_hook_result(
                            exit_code=stop_response.hook_result.exit_code,
                            stdout=stop_response.hook_result.stdout,
                            stderr=stop_response.hook_result.stderr,
                            message="Device stop failed",
                        )
                    record_repo.update_result(
                        record_id=record.id,
                        tenant=tenant,
                        env=env,
                        result_status=PublishRecordResult.FAILED.value,
                        result_message=hook_msg,
                        modifier=operator,
                    )
                else:
                    processed += 1

                    hook_msg = None
                    if stop_response.hook_result:
                        hook_msg = serialize_hook_result(
                            exit_code=stop_response.hook_result.exit_code,
                            stdout=stop_response.hook_result.stdout,
                            stderr=stop_response.hook_result.stderr,
                            message="Device stopped successfully",
                        )
                    record_repo.update_result(
                        record_id=record.id,
                        tenant=tenant,
                        env=env,
                        result_status=PublishRecordResult.SUCCESS.value,
                        result_message=hook_msg,
                        modifier=operator,
                    )
                    logger.info(
                        f"Device {device_record.id} stopped successfully "
                        f"(record preserved for restart)"
                    )

            except Exception as e:
                failed += 1
                logger.error(f"Error stopping device {device_record.id}: {e}")
                record_repo.update_result(
                    record_id=record.id,
                    tenant=tenant,
                    env=env,
                    result_status=PublishRecordResult.FAILED.value,
                    result_message=str(e)[:500],
                    modifier=operator,
                )

        logger.info(
            f"[stop_batch] done: processed={processed} failed={failed} "
            f"publish_id={publish_id}"
        )
        return BatchResult(
            success=failed == 0,
            processed_count=processed,
            failed_count=failed,
        )

    async def _drain_device(
        self,
        tenant: str,
        device_id: int,
        timeout_seconds: int,
        check_interval: float = 1.0,
    ) -> DrainResult:
        """
        Wait for device sessions to complete (graceful drain).

        Per D-03: Exclude device from load balancing before drain
        Per D-04: Timeout after specified seconds (default 30s)
        Per D-09: No session migration - existing sessions complete or timeout

        Args:
            tenant: Tenant name for isolation
            device_id: Device to drain
            timeout_seconds: Maximum time to wait (default 30s)
            check_interval: Seconds between session checks

        Returns:
            DrainResult with success flag and session count at completion
        """
        start_time = datetime.now()
        check_count = 0

        logger.info(f"[drain_device] device={device_id}, timeout={timeout_seconds}s")

        # In mock mode, skip drain wait entirely
        if is_paas_mock_mode():
            logger.info(
                f"[drain_device] mock mode: skipping drain for device={device_id}"
            )
            return DrainResult(
                success=True,
                sessions_remaining=0,
                duration_seconds=0,
                timeout_reached=False,
            )

        while True:
            # Check for active sessions on this device
            active_sessions = await self._get_active_sessions(
                tenant=tenant, device_id=device_id
            )

            if active_sessions == 0:
                duration = (datetime.now() - start_time).total_seconds()
                logger.info(
                    f"Device {device_id} drained after {check_count} checks, "
                    f"duration={duration:.1f}s"
                )
                return DrainResult(
                    success=True,
                    sessions_remaining=0,
                    duration_seconds=duration,
                    timeout_reached=False,
                )

            # Check timeout
            elapsed = (datetime.now() - start_time).total_seconds()
            if elapsed >= timeout_seconds:
                logger.warning(
                    f"Device {device_id} drain timeout after {elapsed:.1f}s, "
                    f"{active_sessions} sessions still active"
                )
                return DrainResult(
                    success=False,
                    sessions_remaining=active_sessions,
                    duration_seconds=elapsed,
                    timeout_reached=True,
                )

            check_count += 1
            await asyncio.sleep(check_interval)

    async def _get_active_sessions(self, tenant: str, device_id: int) -> int:
        """Get count of active sessions (PENDING, RUNNING) on a device.

        Queries the bot session repository for sessions in PENDING or RUNNING
        status associated with the given device.

        Args:
            tenant: Tenant name for multi-tenant isolation
            device_id: Device internal ID (used to look up device_uuid)

        Returns:
            Count of active sessions, or 0 on error (graceful degradation)
        """
        try:
            # Import here to avoid circular imports
            env = get_current_env()

            # Look up device to get device_uuid (session table stores device_uuid, not device_id)
            device_repo = self._device_repo
            device = device_repo.get_by_id(device_id, tenant=tenant, env=env)

            if device is None:
                logger.warning(
                    f"[get_active_sessions] Device {device_id} not found, "
                    "assuming no active sessions"
                )
                return 0

            # Count active sessions by device_uuid
            session_repo = self._session_repo
            count = session_repo.count_active_sessions_by_device(
                device_uuid=device.device_uuid, tenant=tenant
            )

            logger.info(
                f"[get_active_sessions] device_id={device_id}, "
                f"device_uuid={device.device_uuid}, active_sessions={count}"
            )
            return count

        except Exception as e:
            logger.warning(
                f"[get_active_sessions] Error querying sessions for device {device_id}: {e}"
            )
            # Graceful degradation: return 0 to allow drain to proceed
            # This matches the original placeholder behavior on error
            return 0

    async def handle_device_callback(
        self,
        callback: DeviceCallbackRequest,
    ) -> dict[str, str]:
        """Handle start hook callback from async workers or external systems.

        Drives the state update chain:
        device → publish_record → batch → publish → bot → stage advancement

        Only processes event_type="start" callbacks.
        Idempotent: ignores callbacks for records not in PROCESSING status.
        """
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import (
            PublishNotFoundError,
        )

        env = get_current_env()
        tenant_env = env  # callback uses default env

        logger.info(
            f"[device_callback] device_uuid={callback.device_uuid} "
            f"publish_id={callback.publish_id} event_type={callback.event_type} "
            f"result_status={callback.result_status} tenant={callback.tenant}"
        )

        # Only process start callbacks
        if callback.event_type != "start":
            logger.info(
                f"Ignoring non-start callback: event_type={callback.event_type}"
            )
            return {"status": "ignored", "reason": "only start callbacks processed"}

        # Normalize result_status
        normalized_status = callback.result_status.upper()
        if normalized_status not in ("SUCCESS", "FAILED"):
            return {
                "status": "rejected",
                "reason": f"invalid result_status: {callback.result_status}",
            }

        # Tenant is required in callback request body
        tenant = callback.tenant

        # Find device by UUID with resolved tenant
        device_repo = self._device_repo
        device = device_repo.get_by_device_uuid(
            device_uuid=callback.device_uuid,
            tenant=tenant,
            env=tenant_env,
            status=None,
        )
        if not device:
            raise PublishNotFoundError(f"Device not found: {callback.device_uuid}")

        # Find PROCESSING publish_record by device_id + publish_id
        record_repo = self._publish_record_repo
        publish_record = record_repo.get_processing_record_by_device_and_publish(
            device_id=device.id,
            publish_id=callback.publish_id,
            tenant=tenant,
            env=tenant_env,
        )

        if (
            not publish_record
            or publish_record.result_status != PublishRecordResult.PROCESSING.value
        ):
            # No PROCESSING record for this publish — already processed or not found
            logger.info(
                f"No PROCESSING publish_record for device={callback.device_uuid}, "
                f"publish_id={callback.publish_id} — callback ignored (idempotent)"
            )
            return {"status": "ignored", "reason": "no PROCESSING record found"}

        # Update device status
        if normalized_status == "SUCCESS":
            device_repo.update_device(
                device_id=device.id,
                tenant=tenant,
                env=tenant_env,
                modifier="callback",
                status=DeviceStatus.ACTIVE.value,
            )
            logger.info(f"Device {callback.device_uuid} set to ACTIVE via callback")
        else:
            err_msg = callback.stderr or "Start hook failed"
            device_repo.update_device(
                device_id=device.id,
                tenant=tenant,
                env=tenant_env,
                modifier="callback",
                status=DeviceStatus.FAILED.value,
                err_msg=err_msg,
            )
            logger.info(f"Device {callback.device_uuid} set to FAILED via callback")

        # Update publish_record with serialized hook result (optimistic lock)
        result_message = serialize_hook_result(
            exit_code=callback.exit_code,
            stdout=callback.stdout,
            stderr=callback.stderr,
            message="Hook succeeded"
            if normalized_status == "SUCCESS"
            else "Hook failed",
        )
        updated = record_repo.update_result_if_processing(
            record_id=publish_record.id,
            tenant=tenant,
            env=tenant_env,
            result_status=(
                PublishRecordResult.SUCCESS.value
                if normalized_status == "SUCCESS"
                else PublishRecordResult.FAILED.value
            ),
            result_message=result_message,
            modifier="callback",
        )
        if not updated:
            # Concurrent callback already processed this record
            logger.info(
                f"Publish record {publish_record.id} already processed "
                f"— concurrent callback ignored"
            )
            return {"status": "ignored", "reason": "concurrent callback"}

        # Check batch completion and advance state chain
        batch_id = publish_record.batch_id
        if batch_id is None:
            logger.warning(
                f"Publish record {publish_record.id} has no batch_id, "
                f"skipping batch completion check"
            )
            return {"status": "processed", "warning": "no batch_id"}
        await self._check_batch_completion(
            tenant=tenant,
            batch_id=batch_id,
            publish_id=callback.publish_id,
        )

        return {"status": "processed"}

    async def _check_batch_completion(
        self,
        tenant: str,
        batch_id: int,
        publish_id: int,
    ) -> None:
        """Check if all publish_records in a batch have final results.

        If batch is complete, update batch status and check stage advancement.
        Called both from callback handler and inline after no-hook fast path.
        """
        env = get_current_env()
        record_repo = self._publish_record_repo
        batch_repo = self._publish_batch_repo

        # Check if all records in the batch have final results
        counts = record_repo.count_records_by_batch_id(batch_id, tenant, env)
        processing_count = counts.get("PROCESSING", 0)

        if processing_count > 0:
            # Batch not yet complete — still waiting for callbacks
            return

        # Determine batch result
        failed_count = counts.get("FAILED", 0)
        batch_status = (
            BatchStatus.FAILED.value
            if failed_count > 0
            else BatchStatus.COMPLETED.value
        )

        # Update batch status
        batch = batch_repo.get_by_id(batch_id, tenant, env)
        if batch and batch.status != batch_status:
            batch_repo.update_status(
                batch_id=batch_id,
                tenant=tenant,
                env=env,
                status=batch_status,
                modifier="callback",
            )
            logger.info(f"Batch {batch_id} → {batch_status}")

        # Check stage advancement
        if batch:
            await self._check_stage_advancement(
                tenant=tenant,
                publish_id=publish_id,
                current_stage=batch.stage,
                stage_failed=(batch_status == BatchStatus.FAILED.value),
            )

    async def _check_stage_advancement(
        self,
        tenant: str,
        publish_id: int,
        current_stage: str,
        stage_failed: bool,
    ) -> None:
        """Check if all batches in the current stage are complete and advance."""
        from secbaas.community.api.bot_manage import BotStatus

        env = get_current_env()
        batch_repo = self._publish_batch_repo
        publish_repo = self._publish_repo
        bot_repo = self._bot_repo

        publish_record = publish_repo.get_by_id(publish_id, tenant, env)
        if not publish_record:
            logger.warning(
                f"[check_stage_advancement] publish_id={publish_id} "
                f"not found, stage={current_stage}, stage_failed={stage_failed}"
            )
            return

        logger.info(
            f"[check_stage_advancement] publish_id={publish_id} "
            f"pub_status={publish_record.status} stage={current_stage} "
            f"stage_failed={stage_failed} pub_type={publish_record.publish_type}"
        )

        if stage_failed:
            # Publish failed
            publish_repo.update_status(
                publish_id=publish_id,
                tenant=tenant,
                env=env,
                status=PublishStatus.FAILED.value,
                modifier="callback",
            )

            # Clean up pre-created PENDING publish records that were never
            # processed (batches after the failed one never executed)
            try:
                from secbaas.community.api.publish_manage import PublishRecordResult

                record_repo = self._publish_record_repo
                batches = batch_repo.list_by_publish_id(publish_id, tenant, env)
                for b in batches:
                    pending_records = record_repo.list_by_publish_id_and_batch_id(
                        publish_id,
                        b.id,
                        tenant,
                        env,
                        status=PublishRecordResult.PENDING.value,
                    )
                    for rec in pending_records:
                        record_repo.update_result(
                            record_id=rec.id,
                            tenant=tenant,
                            env=env,
                            result_status=PublishRecordResult.FAILED.value,
                            result_message=(
                                "Publish marked as FAILED before this record "
                                "could be processed"
                            ),
                            modifier="callback",
                        )
            except Exception as cleanup_err:
                logger.warning(
                    f"[check_stage_advancement] Failed to cleanup PENDING records "
                    f"for publish_id={publish_id}: {cleanup_err}"
                )

            # If CREATE type and bot is PENDING, set bot to FAILED
            from secbaas.community.api.device_manage import DeviceStatus

            if publish_record.publish_type == PublishType.CREATE.value:
                bot = bot_repo.get_by_id(publish_record.bot_id, tenant, env)
                if bot and bot.status == BotStatus.PENDING.value:
                    bot_repo.update_status(
                        bot_id=publish_record.bot_id,
                        tenant=tenant,
                        env=env,
                        status=BotStatus.FAILED,
                        modifier="callback",
                    )
            elif publish_record.publish_type == PublishType.STOP.value:
                bot_record = bot_repo.get_by_id(publish_record.bot_id, tenant, env)
                if bot_record:
                    bot_repo.update_status(
                        bot_id=bot_record.id,
                        tenant=tenant,
                        env=env,
                        status=BotStatus.ACTIVE.value,
                        modifier="callback",
                    )
            elif publish_record.publish_type == PublishType.DESTROY.value:
                bot = bot_repo.get_by_id(publish_record.bot_id, tenant, env)
                if bot:
                    logger.warning(
                        f"[destroy_failed_cleanup] bot_id={bot.id} "
                        f"releasing despite DESTROY publish failure publish_id={publish_id}"
                    )
                    bot_repo.complete_destroy(
                        bot_id=bot.id,
                        tenant=tenant,
                        env=env,
                        modifier="callback",
                    )
                    device_repo = self._device_repo
                    devices = device_repo.list_by_bot_id(
                        bot_id=bot.id, tenant=tenant, env=env
                    )
                    for device in devices:
                        try:
                            device_repo.update_status_by_device_uuid(
                                device_uuid=device.device_uuid,
                                tenant=tenant,
                                env=env,
                                status=DeviceStatus.RELEASED.value,
                            )
                            device_repo.soft_delete_by_device_uuid(
                                device_uuid=device.device_uuid,
                                tenant=tenant,
                                env=env,
                                modifier="callback",
                            )
                        except Exception as e:
                            logger.warning(
                                f"[destroy_failed_cleanup] device={device.device_uuid} "
                                f"cleanup failed: {e}"
                            )
            # If UPDATE type, clean up orphaned PENDING clone to free UK slot
            elif publish_record.publish_type == PublishType.UPDATE.value:
                self._cleanup_pending_clone_on_update_failure(
                    tenant=tenant,
                    publish_id=publish_id,
                    operator="callback",
                )
            logger.info(f"Publish {publish_id} → FAILED")
            return

        # Check if all batches in the current stage are complete
        batches = batch_repo.list_by_publish_id(publish_id, tenant, env)
        stage_batches = [b for b in batches if b.stage == current_stage]
        all_complete = all(
            b.status in (BatchStatus.COMPLETED.value, BatchStatus.FAILED.value)
            for b in stage_batches
        )

        if not all_complete:
            return

        # All batches in stage completed successfully — check for next stage
        extra_config = _extra_config_to_publish_config(publish_record.extra_config)

        # Use DB-backed batch query instead of static stage_order to handle
        # auto-compacted pipelines (e.g., 1-device → PROD_FIRST_BATCH only)
        next_pending_stage, next_pending_batches = self._get_pending_batches(
            tenant, publish_id
        )

        if next_pending_stage and next_pending_batches:
            # More stages exist in DB — check pause_for_approval
            stage_config = None
            if extra_config:
                stage_config = extra_config.stages.get(next_pending_stage)

            auto_approve = extra_config and extra_config.auto_approve
            if stage_config and stage_config.pause_for_approval and not auto_approve:
                publish_repo.update_status(
                    publish_id=publish_id,
                    tenant=tenant,
                    env=env,
                    status=PublishStatus.APPROVING.value,
                    modifier="callback",
                )
                logger.info(
                    f"Publish {publish_id} → APPROVING (next stage: {next_pending_stage})"
                )
            else:
                if stage_config and stage_config.pause_for_approval and auto_approve:
                    logger.info(
                        f"Publish {publish_id} auto_approve=True, "
                        f"skipping APPROVING, auto-executing next stage: "
                        f"{next_pending_stage}"
                    )
                # Auto-execute next stage
                cooldown = stage_config.cooldown_seconds if stage_config else 0
                publish_repo.update_status(
                    publish_id=publish_id,
                    tenant=tenant,
                    env=env,
                    status=PublishStatus.ACTIVE.value,
                    modifier="callback",
                )
                if cooldown > 0:
                    if is_paas_mock_mode():
                        cooldown = min(cooldown, 1)
                    await asyncio.sleep(cooldown)

                # Check if next stage has actual devices to process.
                # Auto-compacted pipelines may leave empty PENDING batches
                # (e.g., 1-device RESTART with PROD_OTHER_BATCH capacity=0).
                # Empty batches get completed directly to avoid calling
                # execute_stage from callback threads (DB pooling issues).
                all_empty = all(b.batch_capacity == 0 for b in next_pending_batches)
                if all_empty:
                    for b in next_pending_batches:
                        batch_repo.update_status(
                            batch_id=b.id,
                            tenant=tenant,
                            env=env,
                            status=BatchStatus.COMPLETED.value,
                            modifier="callback",
                        )
                    # Recursively advance to check for more stages
                    await self._check_stage_advancement(
                        tenant=tenant,
                        publish_id=publish_id,
                        current_stage=next_pending_stage,
                        stage_failed=False,
                    )
                else:
                    await self.execute_stage(
                        tenant=tenant,
                        publish_id=publish_id,
                        operator="callback",
                    )
        else:
            # All stages complete (no more pending batches in DB)
            if extra_config and extra_config.auto_complete:
                await self.complete_publish(
                    tenant=tenant,
                    publish_id=publish_id,
                    operator="callback",
                )
            else:
                logger.info(
                    f"Publish {publish_id}: all stages complete, auto_complete=False"
                )

    async def complete_publish(
        self,
        tenant: str,
        publish_id: int,
        operator: str,
        publish_record: PublishRecord | None = None,
        bot_record: BotRecord | None = None,
    ) -> PublishResponse:
        """Mark publish as SUCCESS and increment version.

        Per D-05: Auto-increment version on successful completion.
        Per D-04: For DESTROY, mark bot as RELEASED with soft delete.

        This method is idempotent - if publish is already SUCCESS, returns early.

        For DESTROY publishes:
        - Bot transitions from DESTROYING to RELEASED status
        - Bot is soft-deleted (is_deleted = true)
        - If DESTROY publish fails or is rejected/revoked, bot stays in DESTROYING
        - Users should re-approve failed DESTROY publishes to retry destruction
        """
        env = get_current_env()
        logger.info(f"[complete_publish] publish_id={publish_id}, operator={operator}")

        if publish_record is None:
            publish_record, bot_record = self._get_publish_and_bot_record(
                tenant, publish_id
            )
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        # Idempotent: return early if already completed
        if publish_record.status == PublishStatus.SUCCESS.value:
            logger.info(
                f"[complete_publish] publish_id={publish_id} already completed, "
                f"returning current state"
            )
            return await self._refresh_publish_response(tenant, publish_id)

        # Update status to SUCCESS
        publish_repo = self._publish_repo
        publish_repo.update_status(
            publish_id=publish_id,
            tenant=tenant,
            env=env,
            status=PublishStatus.SUCCESS.value,
            modifier=operator,
        )

        # Get bot_repo once for all branches (only if bot operations needed)
        bot_repo = None
        if bot_record is None:
            bot_repo = self._bot_repo
            bot_record = bot_repo.get_by_id(
                publish_record.bot_id, tenant=tenant, env=env
            )
        if bot_repo is None and bot_record is not None:
            bot_repo = self._bot_repo

        # Per D-04: Handle DESTROY cleanup — single atomic transaction
        if publish_record.publish_type == PublishType.STOP.value:
            try:
                if bot_record and bot_repo is not None:
                    bot_repo.complete_stop(
                        bot_id=publish_record.bot_id,
                        tenant=tenant,
                        env=env,
                        modifier=operator,
                    )
                    logger.info(f"Bot {publish_record.bot_id} set to STOPPED")
            except Exception as e:
                logger.warning(f"Failed to stop bot: {e}")

        elif publish_record.publish_type == PublishType.DESTROY.value:
            try:
                if bot_record and bot_repo is not None:
                    bot_repo.complete_destroy(
                        bot_id=publish_record.bot_id,
                        tenant=tenant,
                        env=env,
                        modifier=operator,
                    )
                    logger.info(
                        f"Bot {publish_record.bot_id} set to RELEASED and soft-deleted"
                    )
            except Exception as e:
                logger.warning(f"Failed to cleanup bot after DESTROY: {e}")
        else:
            from secbaas.community.api.bot_manage import BotStatus

            # Handle RESTART publish: if bot was STOPPED, set DB status to ACTIVE so
            # _calculate_bot_status recalculates from device state (newly provisioned devices)
            if publish_record.publish_type == PublishType.RESTART.value:
                if bot_record and bot_repo is not None:
                    try:
                        bot_repo.update_status(
                            bot_id=publish_record.bot_id,
                            tenant=tenant,
                            env=env,
                            status=BotStatus.ACTIVE.value,
                            modifier=operator,
                        )
                        logger.info(
                            f"Bot {publish_record.bot_id} status set to ACTIVE after restart"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to update bot status after RESTART: {e}"
                        )

            # Handle UPDATE publish: transfer relationships from old bot to new
            if publish_record.publish_type == PublishType.UPDATE.value:
                target_bot_id = (publish_record.extra_config or {}).get("target_bot_id")

                if target_bot_id and bot_record and bot_repo is not None:
                    try:
                        rel_repo = self._rel_repo
                        old_rels = rel_repo.list_by_bot_id(
                            bot_id=publish_record.bot_id,
                            tenant=tenant,
                            env=env,
                        )
                        device_uuids = [rel.device_uuid for rel in old_rels]
                        domain = old_rels[0].domain if old_rels else bot_record.domain

                        if not device_uuids:
                            logger.warning(
                                f"UPDATE publish {publish_id}: source bot "
                                f"{publish_record.bot_id} has no device relationships. "
                                f"Marking target bot {target_bot_id} as FAILED."
                            )
                            bot_repo.update_status(
                                bot_id=target_bot_id,
                                tenant=tenant,
                                env=env,
                                status=BotStatus.FAILED,
                                modifier=operator,
                            )
                            bot_repo.complete_destroy(
                                bot_id=publish_record.bot_id,
                                tenant=tenant,
                                env=env,
                                modifier=operator,
                            )
                            return await self._refresh_publish_response(
                                tenant, publish_id
                            )

                        bot_repo.complete_update_transfer(
                            old_bot_id=publish_record.bot_id,
                            new_bot_id=target_bot_id,
                            device_uuids=device_uuids,
                            domain=domain,
                            tenant=tenant,
                            env=env,
                            modifier=operator,
                        )
                        logger.info(
                            f"UPDATE publish {publish_id}: transferred "
                            f"{len(device_uuids)} devices from old bot "
                            f"{publish_record.bot_id} to new bot {target_bot_id}"
                        )
                    except Exception:
                        logger.exception(
                            f"Failed to transfer relationships for UPDATE publish "
                            f"{publish_id}"
                        )
                        raise
                elif not target_bot_id:
                    logger.warning(
                        f"No target_bot_id for UPDATE publish {publish_id}, "
                        f"setting old bot ACTIVE"
                    )
                    if bot_record and bot_repo is not None:
                        bot_repo.update_status(
                            bot_id=publish_record.bot_id,
                            tenant=tenant,
                            env=env,
                            status=BotStatus.ACTIVE,
                            modifier=operator,
                        )
            else:
                if (
                    publish_record.publish_type != PublishType.UPDATE_DEVICE.value
                    and bot_record
                    and bot_repo is not None
                ):
                    try:
                        bot_repo.update_status(
                            bot_id=publish_record.bot_id,
                            tenant=tenant,
                            env=env,
                            status=BotStatus.ACTIVE,
                            modifier=operator,
                        )
                        logger.info(f"Bot {publish_record.bot_id} status set to ACTIVE")
                    except Exception:
                        logger.exception(
                            f"Failed to set bot {publish_record.bot_id} ACTIVE"
                        )

                if publish_record.publish_type in (
                    PublishType.SCALE_UP.value,
                    PublishType.SCALE_DOWN.value,
                ):
                    target_count = publish_record.replica_desired
                    if target_count is not None:
                        try:
                            bot_repo.update_bot(
                                bot_id=publish_record.bot_id,
                                tenant=tenant,
                                env=env,
                                replica_desired=target_count,
                                modifier=operator,
                            )
                            logger.info(
                                f"Bot {publish_record.bot_id} replica_desired "
                                f"updated to {target_count}"
                            )
                        except Exception:
                            logger.exception(
                                f"Failed to update replica_desired "
                                f"for bot {publish_record.bot_id}"
                            )

        return PublishResponse(
            id=publish_record.id,
            bot_id=publish_record.bot_id,
            publish_type=publish_record.publish_type,
            status=PublishStatus.SUCCESS.value,
            stage=PublishStage.SUCCESS.value,
            extra_config=_extra_config_to_publish_config(publish_record.extra_config),
            creator=publish_record.creator,
            modifier=operator,
            gmt_create=publish_record.gmt_create,
            gmt_modified=datetime.now(),
        )

    # ====================================================================
    # TIMEOUT DETECTION
    # ====================================================================
    async def _check_and_handle_timeout(
        self,
        publish_record: PublishRecord,
        tenant: str,
    ) -> None:
        """Check for stale PROCESSING records and fire synthetic FAILED callbacks.

        Records stuck in PROCESSING beyond the configured callback_timeout_seconds
        are treated as timed out. For each, a synthetic DeviceCallbackRequest
        is constructed and passed through handle_device_callback to reuse the
        full callback pipeline (device update, record update, batch/stage check).
        """
        env = get_current_env()
        timeout_seconds = 600  # default

        publish_config = _extra_config_to_publish_config(publish_record.extra_config)
        if publish_config is not None:
            timeout_seconds = publish_config.callback_timeout_seconds

        record_repo = self._publish_record_repo
        stale_records = record_repo.list_stale_processing_records(
            publish_id=publish_record.id,
            timeout_seconds=timeout_seconds,
            tenant=tenant,
            env=env,
        )

        if not stale_records:
            return

        logger.warning(
            f"[timeout] Found {len(stale_records)} stale PROCESSING records "
            f"for publish_id={publish_record.id}, timeout={timeout_seconds}s"
        )

        for record in stale_records:
            if not record.device_uuid:
                logger.warning(f"[timeout] Skipping record {record.id}: no device_uuid")
                continue

            callback = DeviceCallbackRequest(
                device_uuid=record.device_uuid,
                publish_id=publish_record.id,
                event_type="start",
                result_status="FAILED",
                exit_code=-1,
                stderr=f"Callback timeout after {timeout_seconds}s",
                tenant=tenant,
            )

            try:
                await self.handle_device_callback(callback)
                logger.info(
                    f"[timeout] Synthetic FAILED callback processed for "
                    f"record={record.id}, device_uuid={record.device_uuid}"
                )
            except Exception:
                logger.exception(
                    f"[timeout] Failed to process synthetic callback for "
                    f"record={record.id}"
                )

    # ====================================================================
    # PROGRESS TRACKING
    # ====================================================================
    async def get_publish_progress(
        self,
        tenant: str,
        publish_id: int,
        include_devices: bool = False,
    ) -> PublishProgressResponse | None:
        """
        Get detailed progress statistics for a publish operation.

        Aggregates data from baas_publish, baas_publish_batch, and baas_publish_record
        to provide comprehensive progress tracking.

        Args:
            tenant: Tenant name for isolation
            publish_id: Publish ID to query
            include_devices: If True, include device-level details for retry scenarios

        Returns:
            PublishProgressResponse with detailed progress, or None if not found
        """
        env = get_current_env()
        logger.info(
            f"[get_publish_progress] tenant={tenant}, env={env}, publish_id={publish_id}, "
            f"include_devices={include_devices}"
        )

        # Get publish record
        publish_repo = self._publish_repo
        publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
        if publish_record is None:
            logger.warning(f"Publish not found: {publish_id}")
            return None

        # Verify tenant via bot ownership
        bot_repo = self._bot_repo
        bot_record = bot_repo.get_by_id_including_deleted(
            publish_record.bot_id, tenant=tenant, env=env
        )
        if bot_record is None:
            logger.warning(
                f"Tenant mismatch for publish: tenant={tenant}, publish={publish_id}"
            )
            return None

        # Check for timed-out PROCESSING records and fire synthetic callbacks
        await self._check_and_handle_timeout(publish_record, tenant)

        # Re-read publish record after timeout handling (status may have changed)
        publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
        logger.info(
            f"[get_publish_progress_after_timeout] publish_id={publish_id} "
            f"status={publish_record.status if publish_record else 'GONE'}"
        )
        if publish_record is None:
            logger.warning(
                f"[get_publish_progress] publish_id={publish_id} "
                f"disappeared after timeout handling"
            )
            return None

        # Get all batches for this publish
        batch_repo = self._publish_batch_repo
        batches = batch_repo.list_by_publish_id(publish_id, tenant, env)

        # Get record counts
        record_repo = self._publish_record_repo
        status_counts = record_repo.count_records_by_publish_id(publish_id, tenant, env)

        logger.info(
            f"[get_publish_progress] publish_id={publish_id} "
            f"status={publish_record.status} "
            f"status_counts={status_counts} "
            f"stage={self._get_current_stage(tenant, publish_id)}"
        )

        # Aggregate stage-level progress
        stages = self._aggregate_stage_progress(
            batches, publish_id, tenant, record_repo
        )

        # Compute overall progress
        overall = self._compute_overall_progress(batches, status_counts)

        # Build timeline
        timeline = ProgressTimeline(
            gmt_create=publish_record.gmt_create,
            gmt_modified=publish_record.gmt_modified,
            estimated_remaining_seconds=None,  # Could be computed based on average batch time
        )

        # Get current stage
        current_stage = self._get_current_stage(tenant, publish_id)

        # Build device details if requested
        device_details: list[BatchDeviceProgress] = []
        failed_devices: list[DeviceOperationResult] = []

        if include_devices:
            device_details, failed_devices = self._get_device_details(
                batches, tenant, record_repo
            )

        return PublishProgressResponse(
            publish_id=publish_record.id,
            status=publish_record.status,
            current_stage=current_stage,
            overall_progress=overall,
            stages=stages,
            timeline=timeline,
            device_details=device_details,
            failed_devices=failed_devices,
        )

    def _aggregate_stage_progress(
        self,
        batches: list[PublishBatchRecord],
        publish_id: int,
        tenant: str,
        record_repo: Any,
    ) -> list[StageProgress]:
        """Group batches by stage and compute per-stage statistics."""
        env = get_current_env()

        # Group batches by stage
        stage_batches: dict[str, list[PublishBatchRecord]] = {}
        for batch in batches:
            stage = batch.stage
            if stage not in stage_batches:
                stage_batches[stage] = []
            stage_batches[stage].append(batch)

        stages = []
        for stage_name, stage_batch_list in stage_batches.items():
            batches_total = len(stage_batch_list)
            batches_completed = sum(
                1 for b in stage_batch_list if b.status == BatchStatus.COMPLETED.value
            )

            # Determine stage status
            if batches_completed == batches_total:
                stage_status = PublishStatus.SUCCESS.value
            elif batches_completed > 0:
                stage_status = PublishStatus.ACTIVE.value
            else:
                stage_status = PublishStatus.PENDING.value

            # Get device counts for this stage
            stage_device_total = 0
            stage_device_processed = 0
            stage_device_failed = 0

            for batch in stage_batch_list:
                stage_device_total += batch.batch_capacity
                batch_counts = record_repo.count_records_by_batch_id(
                    batch.id, tenant, env
                )
                processed_without_pending = sum(
                    batch_counts.values()
                ) - batch_counts.get("PENDING", 0)
                stage_device_processed += processed_without_pending
                stage_device_failed += batch_counts.get("FAILED", 0)

            stages.append(
                StageProgress(
                    stage=stage_name,
                    status=stage_status,
                    batches_completed=batches_completed,
                    batches_total=batches_total,
                    devices_processed=stage_device_processed,
                    devices_failed=stage_device_failed,
                    devices_total=stage_device_total,
                )
            )

        return stages

    def _compute_overall_progress(
        self, batches: list[PublishBatchRecord], status_counts: dict[str, int]
    ) -> ProgressSummary:
        """Compute overall progress statistics across all batches."""
        total_batches = len(batches)
        completed_batches = sum(
            1 for b in batches if b.status == BatchStatus.COMPLETED.value
        )

        total_devices = sum(b.batch_capacity for b in batches)
        processed_devices = sum(status_counts.values()) - status_counts.get(
            "PENDING", 0
        )
        failed_devices = status_counts.get("FAILED", 0)

        # Compute progress percentage
        if total_devices > 0:
            progress_percentage = (processed_devices / total_devices) * 100.0
        else:
            progress_percentage = 0.0

        return ProgressSummary(
            total_batches=total_batches,
            completed_batches=completed_batches,
            total_devices=total_devices,
            processed_devices=processed_devices,
            failed_devices=failed_devices,
            progress_percentage=min(100.0, progress_percentage),
        )

    def _get_device_details(
        self,
        batches: list[PublishBatchRecord],
        tenant: str,
        record_repo: Any,
    ) -> tuple[list[BatchDeviceProgress], list[DeviceOperationResult]]:
        """Fetch device-level operation details for all batches.

        Returns:
            Tuple of (batch_device_progress_list, failed_devices_list)
        """

        env = get_current_env()

        device_repo = self._device_repo
        device_details: list[BatchDeviceProgress] = []
        failed_devices: list[DeviceOperationResult] = []

        # Collect all device_ids across batches for a single batch query
        all_device_ids: set[int] = set()
        batch_records_map: dict[int, list[PublishRecordRecord]] = {}
        for batch in batches:
            batch_records = record_repo.list_by_batch_id(batch.id, tenant, env)
            batch_records_map[batch.id] = batch_records
            for rec in batch_records:
                if rec.device_id:
                    all_device_ids.add(rec.device_id)

        # Single query to resolve all device_uuids
        device_map = (
            device_repo.get_by_ids(list(all_device_ids), tenant, env)
            if all_device_ids
            else {}
        )

        for batch in batches:
            batch_records = batch_records_map[batch.id]

            batch_devices: list[DeviceOperationResult] = []
            for rec in batch_records:
                device_rec = device_map.get(rec.device_id) if rec.device_id else None
                device_uuid = device_rec.device_uuid if device_rec else None

                device_result = DeviceOperationResult(
                    device_id=rec.device_id,
                    device_uuid=device_uuid,
                    event_type=rec.event_type,
                    result_status=rec.result_status,
                    result_message=rec.result_message,
                    old_device_id=None,  # Would need to parse from extra_config
                    new_device_id=None,  # Would need to parse from extra_config
                    gmt_create=rec.gmt_create,
                )
                batch_devices.append(device_result)

                # Track failed devices for easy retry access
                if rec.result_status == PublishRecordResult.FAILED.value:
                    failed_devices.append(device_result)

            device_details.append(
                BatchDeviceProgress(
                    batch_id=batch.id,
                    batch_index=batch.batch_index,
                    stage=batch.stage,
                    status=batch.status,
                    devices=batch_devices,
                )
            )

        return device_details, failed_devices
