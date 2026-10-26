"""PublishUpdates responsibilities for the composed service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from secbaas.community.core.repository.bot import BotRecord
    from secbaas.community.core.repository.publish import PublishRecord

from secbaas.community.api.publish_manage import (
    BatchResult,
    PublishNotFoundError,
    PublishRecordResult,
    PublishType,
    serialize_hook_result,
)
from secbaas.community.logger import get_logger

logger = get_logger("core-service")


class PublishUpdates:
    """Internal mixin; dependencies and sibling methods belong to the host service."""

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
        env = self._get_current_env()
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
                device_repo.update_device(
                    device_id=device_record.id,
                    tenant=tenant,
                    env=env,
                    extra_config=new_device_extra_config.model_dump(exclude_none=True),
                    modifier=operator,
                )
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
        env = self._get_current_env()
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

            image_config_ready = False
            try:
                # A native restart has no request image override. Clear the
                # persisted device override before restart/retry reads it, while
                # leaving template defaults and all other device fields intact.
                extra_config = dict(device_record.extra_config or {})
                deploy_config = extra_config.get("deploy_config")
                if (
                    isinstance(deploy_config, dict)
                    and deploy_config.get("docker_image") is not None
                ):
                    extra_config["deploy_config"] = {
                        **deploy_config,
                        "docker_image": None,
                    }
                    device_repo.update_device(
                        device_id=device_record.id,
                        tenant=tenant,
                        env=env,
                        extra_config=extra_config,
                        modifier=operator,
                    )
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
                # A failed configuration write must never re-provision from
                # the stale image through the retry path.
                retried = image_config_ready and await self._drive_record_attempt(
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
        env = self._get_current_env()
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
