"""Publish workflow: queries responsibilities."""

from __future__ import annotations

from typing import Any

from secbaas.community.api.publish_manage import (
    DEFAULT_CALLBACK_TIMEOUT_SECONDS,
    BatchDeviceProgress,
    BatchStatus,
    BotPublishSummary,
    DeviceCallbackRequest,
    DeviceOperationResult,
    ProgressSummary,
    ProgressTimeline,
    PublishConfig,
    PublishNotFoundError,
    PublishProgressResponse,
    PublishRecordResult,
    PublishResponse,
    PublishStage,
    PublishStatus,
    StageProgress,
)
from secbaas.community.core.repository.bot import BotRecord
from secbaas.community.core.repository.publish import PublishRecord
from secbaas.community.core.repository.publish_batch import PublishBatchRecord
from secbaas.community.core.repository.publish_record import PublishRecordRecord
from secbaas.community.logger import get_logger

from ._publish_common import _extra_config_to_publish_config
from ._publish_retry_orchestrator import _retry_field

logger = get_logger("core-service")


class PublishQueriesMixin:
    def _get_current_stage(self, tenant: str, publish_id: int) -> str | None:
        """Get current pipeline stage from the first non-completed batch.

        Returns the stage of the first batch that hasn't completed.
        Returns None if all batches are complete or no batches exist.
        """
        env = self._current_env()
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
        env = self._current_env()
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
        env = self._current_env()
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

        env = self._current_env()
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
        env = self._current_env()
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
        env = self._current_env()
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
        env = self._current_env()
        repo = self._publish_repo
        publish_record = repo.get_by_id(publish_id, tenant=tenant, env=env)
        if publish_record is None:
            raise PublishNotFoundError(publish_id)
        return self._build_publish_response(publish_record)

    async def get_publish(self, tenant: str, publish_id: int) -> PublishResponse | None:
        """Get publish by ID with tenant verification (full, for API responses)."""
        env = self._current_env()
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

    async def list_publishes(
        self,
        tenant: str,
        bot_id: int | None = None,
        status: PublishStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[PublishResponse]:
        """List publishes for tenant with optional filtering."""
        env = self._current_env()
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
        env = self._current_env()
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
        env = self._current_env()
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
        env = self._current_env()

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

        env = self._current_env()

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
        env = self._current_env()
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
