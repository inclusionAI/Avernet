"""Publish workflow: restart scale responsibilities."""

from __future__ import annotations

from typing import Any

from secbaas.community.api.publish_manage import (
    BatchResult,
    PublishNotFoundError,
    PublishRecordResult,
    PublishType,
    serialize_hook_result,
)
from secbaas.community.core.repository.bot import BotRecord
from secbaas.community.core.repository.publish import PublishRecord
from secbaas.community.logger import get_logger

logger = get_logger("core-service")


class PublishRestartScaleMixin:
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
        env = self._current_env()
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
                retried = await self._drive_record_attempt(
                    record=record,
                    tenant=tenant,
                    operator=operator,
                    publish_id=publish_id,
                    failure_reason=str(e),
                )
                if retried:
                    logger.warning(
                        f"[restart_batch] retrying device {device_record.device_uuid} "
                        f"after failure: {e}"
                    )
                    continue
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
        env = self._current_env()
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
                        retried = await self._drive_record_attempt(
                            record=record,
                            tenant=tenant,
                            operator=operator,
                            publish_id=publish_id,
                            failure_reason=(result.err_msg or "Device start failed"),
                        )
                        if retried:
                            logger.warning(
                                f"[scale_batch] retrying device {device.device_uuid} "
                                f"after failure: {result.err_msg}"
                            )
                            continue
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
                    retried = await self._drive_record_attempt(
                        record=record,
                        tenant=tenant,
                        operator=operator,
                        publish_id=publish_id,
                        failure_reason=str(e),
                    )
                    if retried:
                        logger.warning(
                            f"[scale_batch] retrying device {device.device_uuid} "
                            f"after exception: {e}"
                        )
                        continue
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
