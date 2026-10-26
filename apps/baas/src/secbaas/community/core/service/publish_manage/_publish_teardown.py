"""PublishTeardown responsibilities for the composed service."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from secbaas.community.core.repository.bot import BotRecord
    from secbaas.community.core.repository.publish import PublishRecord

from secbaas.community.api.publish_manage import (
    BatchResult,
    DrainResult,
    PublishNotFoundError,
    PublishRecordResult,
    serialize_hook_result,
)
from secbaas.community.logger import get_logger

logger = get_logger("core-service")


class PublishTeardown:
    """Internal mixin; dependencies and sibling methods belong to the host service."""

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
        env = self._get_current_env()
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
        env = self._get_current_env()

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
        if self._is_paas_mock_mode():
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
            env = self._get_current_env()

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
