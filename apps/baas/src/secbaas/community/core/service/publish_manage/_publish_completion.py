"""Publish workflow: completion responsibilities."""

from __future__ import annotations

import asyncio
from datetime import datetime

from secbaas.community.api.bot_manage import BotStatus
from secbaas.community.api.publish_manage import (
    BatchStatus,
    DeviceCallbackRequest,
    PublishNotFoundError,
    PublishRecordResult,
    PublishResponse,
    PublishStage,
    PublishStatus,
    PublishType,
    serialize_hook_result,
)
from secbaas.community.core.repository.bot import BotRecord
from secbaas.community.core.repository.publish import PublishRecord
from secbaas.community.logger import get_logger

from ._publish_common import _extra_config_to_publish_config

logger = get_logger("core-service")


class PublishCompletionMixin:
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

        env = self._current_env()
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

            retried = await self._drive_record_attempt(
                record=publish_record,
                tenant=tenant,
                operator="callback",
                publish_id=callback.publish_id,
                failure_reason=err_msg,
            )
            if retried:
                logger.warning(
                    f"[device_callback] retrying device {callback.device_uuid} "
                    f"after hook failure: {err_msg}"
                )
                return {"status": "retrying"}

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
        env = self._current_env()
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

        env = self._current_env()
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
                    if self._is_paas_mock_mode():
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
        env = self._current_env()
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
