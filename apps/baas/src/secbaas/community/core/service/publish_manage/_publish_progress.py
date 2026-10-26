"""PublishProgress responsibilities for the composed service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from secbaas.community.core.repository.publish import PublishRecord

from secbaas.community.api.publish_manage import (
    DEFAULT_CALLBACK_TIMEOUT_SECONDS,
    BatchDeviceProgress,
    BatchStatus,
    DeviceCallbackRequest,
    DeviceOperationResult,
    ProgressSummary,
    ProgressTimeline,
    PublishProgressResponse,
    PublishRecordResult,
    PublishStatus,
    StageProgress,
)
from secbaas.community.core.repository.publish_batch import (
    PublishBatchRecord,
)
from secbaas.community.core.repository.publish_record import (
    PublishRecordRecord,
)
from secbaas.community.logger import get_logger

from ._publish_retry_orchestrator import (
    _retry_field,
)
from ._publish_types import _extra_config_to_publish_config

logger = get_logger("core-service")


class PublishProgress:
    """Internal mixin; dependencies and sibling methods belong to the host service."""

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
        env = self._get_current_env()
        timeout_seconds = DEFAULT_CALLBACK_TIMEOUT_SECONDS  # default

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
                result = await self.handle_device_callback(callback)
                logger.info(
                    f"[timeout] Synthetic FAILED callback processed for "
                    f"record={record.id}, device_uuid={record.device_uuid}, "
                    f"result={result.get('status')}"
                )
            except Exception:
                logger.exception(
                    f"[timeout] Failed to process synthetic callback for "
                    f"record={record.id}"
                )

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
        env = self._get_current_env()
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
        env = self._get_current_env()

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

        env = self._get_current_env()

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
                    publish_max_retry_times=_retry_field(
                        rec, "publish_max_retry_times"
                    ),
                    publish_retry_count=_retry_field(rec, "publish_retry_count"),
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
