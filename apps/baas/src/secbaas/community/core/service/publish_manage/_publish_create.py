"""Publish workflow: create responsibilities."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from secbaas.community.api.bot_manage import BotStatus
from secbaas.community.api.bot_runtime import BotNotFoundError
from secbaas.community.api.publish_manage import (
    DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS,
    BatchStatus,
    PublishBatchConfig,
    PublishConfig,
    PublishConflictError,
    PublishNotFoundError,
    PublishResponse,
    PublishStatus,
    PublishType,
    RestartScope,
)
from secbaas.community.core.repository.bot import BotRecordConflictError
from secbaas.community.core.repository.publish_batch import PublishBatchRecord
from secbaas.community.core.service.bot_manage import try_insert_pending_bot
from secbaas.community.logger import get_logger

from ._creation_defaults import resolve_publish_creation
from ._publish_common import _extra_config_to_publish_config

logger = get_logger("core-service")

_BOT_PUBLISH_LOCK_EXPIRE_SECONDS = 300


class PublishCreateMixin:
    def _cleanup_pending_clone_on_update_failure(
        self,
        tenant: str,
        publish_id: int,
        operator: str,
    ) -> None:
        """Abandon the still-PENDING clone reserved by an UPDATE publish.

        An UPDATE publish reserves its deployment slot by cloning the source
        bot row into a PENDING bot row (create_publish Step 4.5) and recording
        the clone id in ``baas_publish.extra_config["target_bot_id"]``. The
        clone must not stay live once its publish reaches a terminal outcome,
        or it permanently occupies the unique key slot
        (tenant, bot_uuid, env, status=PENDING, is_deleted=0) and every later
        publish for the bot_uuid fails with a duplicate key violation.

        Callers cover every terminal path of an UPDATE publish: stage and
        execution failures (FAILED), the create-time orphan auto-clean and
        stale auto-fail, rejection (REJECTED), revocation (REVOKED), and the
        admin force-success abandonment. Per design D2 the call happens
        BEFORE the terminal status write, so a cleanup failure leaves the
        publish non-terminal and the error surfaces.

        Defensive no-ops: the publish is missing, its extra_config has no
        ``target_bot_id`` pointer (crash window between the clone insert and
        the pointer write), the target row is gone, or the target is no
        longer live PENDING — another terminal path may have resolved it or
        the transfer may have promoted it.

        Also soft-deletes any bot_device_rel rows on the clone (should
        always be zero since device transfer happens only at SUCCESS, but the
        cleanup is harmless and future-proof).
        """

        env = self._current_env()
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
            f"[cleanup_pending] Soft-deleted still-PENDING clone bot {target_bot_id} "
            f"abandoned by UPDATE publish {publish_id} terminal resolution"
        )

    def _owns_live_device_binding(self, bot_id: int, tenant: str, env: str) -> bool:
        """True when the generation still holds device ownership nothing else does.

        Device ownership is authoritative in ``baas_bot_device_rel`` (the
        restore-resolution precedent, #2648: live rel rows identify the record
        generation that actually served devices). A rel row is *ownership* only
        while it is the device's live binding; a rel whose device is already
        bound by another live generation is a stale duplicate left by a
        corruption shape and may be tombstoned together with the swept row.
        """
        for rel in self._rel_repo.list_by_bot_id(bot_id, tenant, env):
            binding = self._rel_repo.get_by_device_uuid(rel.device_uuid, tenant, env)
            if binding is not None and binding.bot_id == bot_id:
                return True
        return False

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
        env = self._current_env()
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

        if publish_type == PublishType.UPDATE and config and config.template_uuid:
            # COSEC: Resolve the requested template within the current tenant before
            # persisting publish state, preventing cross-tenant template reuse.
            target_template = self._template_service.get_online_template_by_uuid(
                tenant=tenant,
                template_uuid=config.template_uuid,
            )
            if target_template is None:
                raise ValueError(
                    f"Template not found or not online: uuid={config.template_uuid}, "
                    f"tenant={tenant}"
                )

        lock_name = f"bot:publish:{tenant}:{env}:{bot.bot_uuid}"
        with self._lock_service.try_lock(
            lock_name, expire_seconds=_BOT_PUBLISH_LOCK_EXPIRE_SECONDS
        ) as lock:
            if not lock.acquired:
                raise PublishConflictError(
                    f"Cannot create {publish_type.value} publish for bot "
                    f"{bot.bot_uuid}: another publish is being created concurrently"
                )
            return await self._create_publish_locked(
                tenant=tenant,
                bot=bot,
                bot_id=bot_id,
                publish_type=publish_type,
                operator=operator,
                config=config,
                env=env,
            )

    async def _create_publish_locked(
        self,
        *,
        tenant: str,
        bot: Any,
        bot_id: int,
        publish_type: PublishType,
        operator: str,
        config: PublishConfig | None,
        env: str,
    ) -> PublishResponse:
        # Step 2: Check for concurrent active publish (SVC-PUB-15).
        # Generation-coverage per design D5.1: an UPDATE publish's bot_id is
        # its SOURCE bot row id, so a single-id lookup can miss a non-terminal
        # publish attached to another live generation of the same bot_uuid.
        # Resolve ALL live generation ids for the uuid, list the non-terminal
        # publishes pointing at any of them (id descending, newest first), and
        # route every returned active through the existing four-branch
        # classifier below unchanged. Orphan and stale resolutions fall
        # through to re-query; a different-type conflict raises and a
        # same-type repeat returns idempotently.
        publish_repo = self._publish_repo
        # Termination guard: each active publish is processed at most once, so the
        # loop cannot spin when a status write does not take effect; an
        # unresolved repeat later surfaces at the classifier's conflict or
        # idempotent branches.
        processed_active_ids: set[int] = set()
        while True:
            generation_ids = [
                r.id for r in self._bot_repo.list_by_bot_uuid(bot.bot_uuid, tenant, env)
            ]
            actives = [
                p
                for p in publish_repo.list_active_by_bot_ids(
                    generation_ids, tenant, env
                )
                if p.id not in processed_active_ids
            ]
            if not actives:
                break
            active_publish = actives[0]
            processed_active_ids.add(active_publish.id)
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
                    if active_publish.publish_type == PublishType.UPDATE.value:
                        # Abandon the still-PENDING clone before the terminal
                        # FAILED write (design D2), mirroring the orphan branch
                        # above, so the new publish's own clone reservation
                        # cannot collide with the leaked slot. A cleanup
                        # error surfaces: the stale publish stays non-terminal.
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

        # Reconcile sweep for unclaimed clones (design D5, strictly BEFORE the
        # Step 4 insert so the publish this call creates can never sweep its
        # own clone): the classifier above proved there is no non-terminal
        # publish for any LIVE generation, so a still-live PENDING row of the
        # uuid is garbage from a past terminal-path leak or the creation crash
        # window. The protection set (D5.6/D5.7) is computed independently
        # over ALL generations including retired/soft-deleted ones: a uuid
        # with ANY unfinished publish is in-flight, so every clone claimed by
        # one of those publishes stays untouched — an admin-retired source
        # mid-update must not lose its clone to this sweep. The resolved
        # source row is always protected too (a never-published first
        # generation PENDING row fronts this very CREATE publish). Device
        # safety: a generation that still owns live
        # ``baas_bot_device_rel`` rows is protected as well — sweeping it
        # would tombstone its rels and detach live devices. If a concurrent
        # competitor claimed a clone in the race window, the unique keys
        # arbitrate — never a delete.
        all_generations = self._bot_repo.list_by_bot_uuid_including_deleted(
            bot.bot_uuid, tenant, env
        )
        all_generation_ids = [r.id for r in all_generations]
        protected_bot_ids: set[int] = {bot_id}
        for active in publish_repo.list_active_by_bot_ids(
            all_generation_ids, tenant, env
        ):
            target_bot_id = (active.extra_config or {}).get("target_bot_id")
            if isinstance(target_bot_id, int):
                protected_bot_ids.add(target_bot_id)
        for generation_id in all_generation_ids:
            if self._owns_live_device_binding(generation_id, tenant, env):
                protected_bot_ids.add(generation_id)
        reconciled = self._bot_repo.reconcile_unclaimed_pending_bots(
            bot.bot_uuid,
            tenant,
            env,
            modifier=operator,
            exclude_bot_ids=protected_bot_ids,
        )
        if reconciled:
            logger.warning(
                f"Reconcile sweep reclaimed {reconciled} unclaimed PENDING clone(s) "
                f"for bot {bot.bot_uuid} before creating {publish_type.value} publish"
            )

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
                # Generation-coverage per design D5.1: consult the same
                # uuid-scoped resolver as Step 2 (a live publish attached to
                # any generation of the uuid means an update may be touching
                # this bot's devices right now), not only the current row id.
                restart_generation_ids = [
                    r.id
                    for r in self._bot_repo.list_by_bot_uuid(bot.bot_uuid, tenant, env)
                ]
                restart_actives = publish_repo.list_active_by_bot_ids(
                    restart_generation_ids, tenant, env
                )
                if restart_actives:
                    logger.info(
                        f"[create_publish] RESTART bot_id={bot.id} has active publish "
                        f"{restart_actives[0].id}, excluding UPDATING devices"
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
        if publish_type in (PublishType.UPDATE, PublishType.UPDATE_DEVICE):
            from secbaas.community.core.service.bot_manage import merge_deploy_config

            # The direct publish API also needs request-scoped image semantics.
            # Persist an explicit null for a new update with no deployment config;
            # an absent key remains the compatibility signal for legacy publishes.
            deploy_config = merge_deploy_config(
                bot.config.deploy_config if bot.config else None,
                config.deploy_config if config else None,
            )
            extra_config["deploy_config"] = (
                deploy_config.model_dump(exclude_none=True)
                if deploy_config is not None
                else None
            )

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
            **resolve_publish_creation(
                publish_type=publish_type.value,
                creator=operator,
                name=bot_name,
                replica_desired=replica_desired,
                batch_capacity=batch_capacity,
                batch_number=batch_number,
                cooldown_seconds=cooldown_seconds,
            ),
            config_version=config_version,
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
            # Preserve the old clone's metadata when no deployment override was
            # supplied, but never let clone fallback restore its previous image.
            new_bot_extra_config = (
                new_bot_config.model_dump(exclude_none=True)
                if new_bot_config is not None
                else dict(bot.config.model_dump(exclude_none=True))
                if bot.config
                else {}
            )
            new_bot_extra_config["deploy_config"] = extra_config["deploy_config"]
            try:
                new_bot_id = try_insert_pending_bot(
                    self._bot_repo,
                    source_bot_id=bot_id,
                    tenant=tenant,
                    env=env,
                    extra_config=new_bot_extra_config,
                    name=config.bot_name if config else None,
                    template_uuid=config.template_uuid if config else None,
                    modifier=operator,
                )
            except BotRecordConflictError as exc:
                raise PublishConflictError(
                    f"Cannot create {publish_type.value} publish for bot "
                    f"{bot.bot_uuid}: a PENDING update record already exists"
                ) from exc
            merge_config["target_bot_id"] = new_bot_id
            publish_repo.update_publish(
                publish_id=publish_id,
                tenant=tenant,
                env=env,
                extra_config=merge_config,
                modifier=operator,
            )
            logger.info(
                f"Created PENDING bot record for UPDATE publish: "
                f"target_bot_id={new_bot_id}, publish_id={publish_id}"
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
            publish_config=config,
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
