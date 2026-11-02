"""Publish workflow: execute responsibilities."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from secbaas.community.api.bot_manage import BotStatus
from secbaas.community.api.publish_manage import (
    BatchResult,
    BatchStatus,
    DrainResult,
    PublishConfig,
    PublishNotFoundError,
    PublishStatus,
    PublishType,
)
from secbaas.community.core.repository.bot import BotRecord
from secbaas.community.core.repository.publish import PublishRecord
from secbaas.community.core.repository.publish_batch import PublishBatchRecord
from secbaas.community.logger import get_logger

logger = get_logger("core-service")


class PublishExecuteMixin:
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
        env = self._current_env()
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
                elif (
                    current_batch
                    and current_batch.status == BatchStatus.COMPLETED.value
                ):
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
                if self._is_paas_mock_mode():
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
