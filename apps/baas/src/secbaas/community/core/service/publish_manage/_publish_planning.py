"""PublishPlanning responsibilities for the composed service."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from secbaas.community.core.repository.bot import BotRecord

from secbaas.community.api.publish_manage import (
    PublishConfig,
    PublishEventType,
    PublishRecordResult,
    PublishStage,
    PublishType,
    RestartScope,
    StageConfig,
)
from secbaas.community.core.repository.publish_batch import (
    PublishBatchRecord,
)
from secbaas.community.core.repository.publish_record import (
    PublishRecordExtraConfig,
)
from secbaas.community.logger import get_logger

from ._publish_types import BatchConfig

logger = get_logger("core-service")


class PublishPlanning:
    """Internal mixin; dependencies and sibling methods belong to the host service."""

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
        publish_config: PublishConfig | None = None,
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

            # Prefer deploy_config from this Scale Publish, then fall back
            # to Bot's persisted config, then to template defaults.
            effective_deploy_config = (
                publish_config.deploy_config
                if publish_config and publish_config.deploy_config
                else bot_config.deploy_config
                if bot_config
                else None
            )

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
                        deploy_config=effective_deploy_config,
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

            publish_repo = self._publish_repo
            publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
            target_uuids: list[str] = []
            if publish_record and publish_record.extra_config:
                try:
                    cfg = PublishConfig.model_validate(publish_record.extra_config)
                    target_uuids = cfg.target_device_uuids or []
                except Exception:
                    logger.warning(
                        f"[create_device_records] Failed to parse "
                        f"target_device_uuids for SCALE_DOWN "
                        f"publish_id={publish_id}"
                    )

            if target_uuids:
                target_set = set(target_uuids)
                present_uuids = {d.device_uuid for d in eligible_devices}
                missing = target_set - present_uuids
                if missing:
                    raise ValueError(
                        f"Requested SCALE_DOWN devices not ACTIVE/owned: "
                        f"{sorted(missing)}"
                    )
                eligible_devices = [
                    d for d in eligible_devices if d.device_uuid in target_set
                ]
                expected_scale_amount = sum(b.batch_capacity for b in batch_records)
                if len(eligible_devices) != expected_scale_amount:
                    raise ValueError(
                        f"SCALE_DOWN target_device_uuids count "
                        f"({len(eligible_devices)}) != scale_amount "
                        f"({expected_scale_amount})"
                    )

            event_type = PublishEventType.DESTROY.value
            logger.info(
                f"[create_device_records] SCALE_DOWN publish_id={publish_id} "
                f"found {len(eligible_devices)} ACTIVE devices"
                + (f" targeted={target_uuids}" if target_uuids else "")
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
                extra_config_dict = dataclasses.asdict(
                    PublishRecordExtraConfig(
                        device_uuid=device.device_uuid,
                        provider_device_id=device.provider_device_id,
                        publish_max_retry_times=(
                            publish_config.publish_max_retry_times
                            if publish_config is not None
                            else 0
                        ),
                    )
                )
                extra_config = (
                    extra_config_dict
                    if extra_config_dict.get("device_uuid") is not None
                    or extra_config_dict.get("provider_device_id") is not None
                    else None
                )
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
                    extra_config=extra_config,
                )

        logger.info(
            f"[create_device_records] publish_id={publish_id} type={publish_type.value} "
            f"created {len(sorted_devices[:device_idx])} PENDING records "
            f"across {len(batch_records)} batches"
        )
