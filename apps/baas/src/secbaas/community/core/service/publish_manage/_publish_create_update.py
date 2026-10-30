"""Publish workflow: create update responsibilities."""

from __future__ import annotations

from typing import Any

from secbaas.community.api.publish_manage import (
    BatchResult,
    PublishNotFoundError,
    PublishRecordResult,
)
from secbaas.community.core.repository.bot import BotRecord
from secbaas.community.core.repository.publish import PublishRecord
from secbaas.community.logger import get_logger

logger = get_logger("core-service")


class PublishCreateUpdateMixin:
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
        env = self._current_env()
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
                    retried = await self._drive_record_attempt(
                        record=record,
                        tenant=tenant,
                        operator=operator,
                        publish_id=publish_id,
                        failure_reason=(result.err_msg or "Device start failed"),
                    )
                    if retried:
                        logger.warning(
                            f"[start_batch] retrying device {device.device_uuid} "
                            f"after failure: {result.err_msg}"
                        )
                        continue
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
                retried = await self._drive_record_attempt(
                    record=record,
                    tenant=tenant,
                    operator=operator,
                    publish_id=publish_id,
                    failure_reason=str(e),
                )
                if retried:
                    logger.warning(
                        f"[start_batch] retrying device {device.id} after exception: {e}"
                    )
                    continue
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
        env = self._current_env()
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

        # UPDATE_DEVICE has no target Bot row. Its immutable publish snapshot
        # must win over the current Bot configuration, which can still contain
        # an older image (or change while an approval is pending).
        publish_config = publish_record.extra_config or {}
        if "deploy_config" in publish_config:
            from secbaas.community.api.device_manage import DeployConfig

            snapshot = publish_config["deploy_config"]
            new_bot_config.deploy_config = (
                DeployConfig.model_validate(snapshot) if snapshot is not None else None
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

            image_config_ready = False
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
                updated_rows = device_repo.update_device(
                    device_id=device_record.id,
                    tenant=tenant,
                    env=env,
                    extra_config=new_device_extra_config.model_dump(exclude_none=True),
                    modifier=operator,
                )
                if updated_rows == 0:
                    raise RuntimeError("Device configuration update affected no rows")
                image_config_ready = True
                # Only expose PROCESSING to callback/timeout retries after
                # the operation's configuration is durable.
                record_repo.update_result(
                    record_id=record.id,
                    tenant=tenant,
                    env=env,
                    result_status=PublishRecordResult.PROCESSING.value,
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
                # Never retry provider provisioning with stale configuration
                # after a failed persistence write.
                retried = image_config_ready and await self._drive_record_attempt(
                    record=record,
                    tenant=tenant,
                    operator=operator,
                    publish_id=publish_id,
                    failure_reason=str(e),
                )
                if retried:
                    logger.warning(
                        f"[update_batch] retrying device {device_record.device_uuid} "
                        f"after failure: {e}"
                    )
                    continue
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
