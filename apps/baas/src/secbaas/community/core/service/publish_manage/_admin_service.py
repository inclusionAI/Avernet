"""Admin service for test/development utilities.

Provides business logic for forcing entity states to success,
bypassing normal state machine transitions.
"""

from __future__ import annotations

from secbaas.community.api.publish_manage import (
    ForceSuccessResult,
    PublishAdminService,
    PublishConflictError,
    PublishNotFoundError,
    RestoreBotResult,
    UpdateBotStatusResult,
    UpdateDeviceStatusResult,
)
from secbaas.community.core.repository.bot import (
    BotRecordConflictError,
    BotRepository,
)
from secbaas.community.core.repository.bot_device_rel import BotDeviceRelRepository
from secbaas.community.core.repository.device import DeviceRepository
from secbaas.community.core.repository.publish import PublishRepository
from secbaas.community.core.repository.publish_batch import PublishBatchRepository
from secbaas.community.core.repository.publish_record import PublishRecordRepository
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

logger = get_logger("core-service")


class DefaultPublishAdminService(PublishAdminService):
    """Admin service for forcing entity states.

    Provides force-success operations that bypass normal state machine
    transitions — use with caution, intended for test/development only.
    """

    def __init__(
        self,
        publish_repo: PublishRepository,
        batch_repo: PublishBatchRepository,
        record_repo: PublishRecordRepository,
        device_repo: DeviceRepository,
        bot_repo: BotRepository,
        rel_repo: BotDeviceRelRepository,
    ) -> None:
        self._publish_repo = publish_repo
        self._batch_repo = batch_repo
        self._record_repo = record_repo
        self._device_repo = device_repo
        self._bot_repo = bot_repo
        self._rel_repo = rel_repo

    async def force_success(
        self,
        *,
        publish_id: int,
        tenant: str,
        modifier: str,
    ) -> ForceSuccessResult:
        """Force a publish and all related entities to their success states.

        Update order (bottom-up):
        1. records → result_status=SUCCESS
        2. batches → status=COMPLETED
        3. devices → status=ACTIVE
        4. bot → status=ACTIVE
        5. publish → status=SUCCESS
        """
        env = get_current_env()

        logger.warning(
            f"ADMIN_FORCE_SUCCESS: publish_id={publish_id} tenant={tenant} modifier={modifier}"
        )

        publish = self._publish_repo.get_by_id(publish_id, tenant, env)
        if publish is None:
            raise PublishNotFoundError(publish_id)

        previous_publish_status = publish.status
        bot_id = publish.bot_id

        batches_updated, records_updated = self._update_batches_and_records(
            publish_id=publish_id,
            tenant=tenant,
            env=env,
            modifier=modifier,
        )

        devices_updated, bot_updated = self._update_devices_and_bot(
            bot_id=bot_id,
            tenant=tenant,
            env=env,
            modifier=modifier,
        )

        self._publish_repo.update_status(
            publish_id=publish_id,
            tenant=tenant,
            env=env,
            status="SUCCESS",
            modifier=modifier,
        )

        return ForceSuccessResult(
            publish_id=publish_id,
            previous_publish_status=previous_publish_status,
            batches_updated=batches_updated,
            records_updated=records_updated,
            devices_updated=devices_updated,
            bot_updated=bot_updated,
        )

    async def update_device_status(
        self,
        *,
        device_uuid: str,
        tenant: str,
        status: str,
        operator: str,
    ) -> UpdateDeviceStatusResult:
        """Update a device's status directly, bypassing normal state machine.

        WARNING: Admin operation for test/development use only.
        Does not trigger any publish workflow or PaaS operation.
        Uses get_by_device_uuid_only() to allow cross-env admin access.
        """
        device = self._device_repo.get_by_device_uuid_only(
            device_uuid=device_uuid,
        )
        if device is None:
            raise PublishNotFoundError(f"Device {device_uuid} not found")

        previous_status = device.status
        logger.warning(
            f"ADMIN_UPDATE_DEVICE_STATUS: device_uuid={device_uuid} "
            f"tenant={device.tenant} env={device.env} "
            f"previous_status={previous_status} "
            f"new_status={status} operator={operator}"
        )

        self._device_repo.update_status_by_device_uuid(
            device_uuid=device_uuid,
            tenant=device.tenant,
            env=device.env,
            status=status,
        )

        return UpdateDeviceStatusResult(
            device_uuid=device_uuid,
            previous_status=previous_status,
            new_status=status,
        )

    async def update_bot_status(
        self,
        *,
        bot_id: int,
        status: str,
        operator: str,
    ) -> UpdateBotStatusResult:
        """Update a bot's status directly, bypassing normal state machine.

        WARNING: Admin operation for test/development use only.
        Does not trigger any publish workflow or PaaS operation.
        Uses get_by_id_only() to allow cross-tenant/env admin access by PK.
        """
        bot = self._bot_repo.get_by_id_only(bot_id)
        if bot is None:
            raise PublishNotFoundError(f"Bot {bot_id} not found")

        previous_status = bot.status
        logger.warning(
            f"ADMIN_UPDATE_BOT_STATUS: bot_id={bot_id} "
            f"bot_uuid={bot.bot_uuid} tenant={bot.tenant} env={bot.env} "
            f"previous_status={previous_status} "
            f"new_status={status} operator={operator}"
        )

        self._bot_repo.update_status(
            bot_id=bot_id,
            tenant=bot.tenant,
            env=bot.env,
            status=status,
            modifier=operator,
        )

        return UpdateBotStatusResult(
            bot_id=bot_id,
            bot_uuid=bot.bot_uuid,
            previous_status=previous_status,
            new_status=status,
        )

    async def restore_bot(
        self,
        *,
        bot_id: int | None = None,
        bot_uuid: str | None = None,
        operator: str,
    ) -> RestoreBotResult:
        """Restore a bot record to live ACTIVE, bypassing the state machine.

        WARNING: Admin recovery tool (like update_bot_status, this bypasses
        normal lifecycle transitions and intentionally deviates from
        "RELEASED is terminal"). Exactly ONE of ``bot_id`` / ``bot_uuid``
        must be provided (the router enforces this; direct callers that
        violate it get a ValueError):

        * ``bot_id`` — restores that record directly.
        * ``bot_uuid`` — resolves the target first, then restores it the
          same way: list every record id sharing the uuid (live AND
          soft-deleted), then take ``MAX(bot_id)`` over the ids that still
          hold a non-deleted device relation. The newest record holding
          live rels is the mistake victim in both known recovery states
          (wrong restore-by-id leaving the newer record's rels live, and a
          failed DESTROY cleanup); a fully-destroyed bot has no live rels
          and correctly maps to not-found.

        The resolved/target record is revived and every OTHER live record
        sharing its bot_uuid is retired in one atomic transaction.

        Cross-env by requirement: the repository lookups are by primary
        key / bot_uuid only and the resolution counts live rels in every
        env — no env validation or filtering anywhere in this chain. The
        repository sets ``modifier`` (the operator identity) on every
        touched row.

        Raises PublishNotFoundError with the selector-specific message when
        the ``bot_uuid`` matches no records or no live device relation, or
        when the target record id does not exist. Unique-key conflicts
        (concurrent restore or publish race) surface as
        :class:`PublishConflictError` after the repository transaction
        has rolled back — no partial state.
        """
        if bot_id is None:
            if bot_uuid is None:
                raise ValueError("exactly one of bot_id or bot_uuid must be provided")
            records = self._bot_repo.list_by_bot_uuid_only_including_deleted(bot_uuid)
            if not records:
                raise PublishNotFoundError(
                    f"No bot records found for bot_uuid={bot_uuid}"
                )
            ids = [r.id for r in records]
            resolved = self._rel_repo.max_live_bot_id_for_bot_ids(ids)
            if resolved is None:
                raise PublishNotFoundError(
                    f"No non-deleted device relation found for bot_uuid={bot_uuid}"
                )
            bot_id = resolved
        elif bot_uuid is not None:
            raise ValueError("exactly one of bot_id or bot_uuid must be provided")

        try:
            snapshot = self._bot_repo.restore_bot_record_by_id(
                bot_id, modifier=operator
            )
        except BotRecordConflictError as exc:
            raise PublishConflictError(
                f"Bot {bot_id} restore conflicts with a live unique-key "
                f"slot; no changes applied"
            ) from exc
        if snapshot is None:
            raise PublishNotFoundError(f"Bot {bot_id} not found")

        logger.warning(
            "ADMIN_RESTORE_BOT: operator=%s bot_id=%s bot_uuid=%s %s->ACTIVE, retired=%s",
            operator,
            bot_id,
            snapshot.record.bot_uuid,
            snapshot.previous_status,
            snapshot.retired_sibling_count,
        )

        return RestoreBotResult(
            bot_id=bot_id,
            bot_uuid=snapshot.record.bot_uuid,
            previous_status=snapshot.previous_status,
            previous_is_deleted=snapshot.previous_is_deleted,
            new_status=snapshot.record.status,
            retired_sibling_count=snapshot.retired_sibling_count,
        )

    def _update_batches_and_records(
        self,
        *,
        publish_id: int,
        tenant: str,
        env: str,
        modifier: str,
    ) -> tuple[int, int]:
        batches = self._batch_repo.list_by_publish_id(publish_id, tenant, env)
        batches_updated = 0
        records_updated = 0

        for batch in batches:
            for record in self._record_repo.list_by_batch_id(batch.id, tenant, env):
                if record.result_status != "SUCCESS":
                    self._record_repo.update_result(
                        record_id=record.id,
                        tenant=tenant,
                        env=env,
                        result_status="SUCCESS",
                        result_message=None,
                        modifier=modifier,
                    )
                    records_updated += 1

            if batch.status != "COMPLETED":
                self._batch_repo.update_status(
                    batch_id=batch.id,
                    tenant=tenant,
                    env=env,
                    status="COMPLETED",
                    modifier=modifier,
                )
                batches_updated += 1

        return batches_updated, records_updated

    def _update_devices_and_bot(
        self,
        *,
        bot_id: int | None,
        tenant: str,
        env: str,
        modifier: str,
    ) -> tuple[int, bool]:
        devices_updated = 0
        bot_updated = False

        if not bot_id:
            return devices_updated, bot_updated

        try:
            for device in self._device_repo.list_by_bot_id(
                bot_id=bot_id, tenant=tenant, env=env
            ):
                if device.status != "ACTIVE":
                    self._device_repo.update_status(
                        device_id=device.id,
                        tenant=tenant,
                        env=env,
                        status="ACTIVE",
                    )
                    devices_updated += 1
        except Exception as e:
            logger.warning(
                f"ADMIN_FORCE_SUCCESS: failed to update devices for bot_id={bot_id}: {e}"
            )

        try:
            self._bot_repo.update_status(
                bot_id=bot_id,
                tenant=tenant,
                env=env,
                status="ACTIVE",
                modifier=modifier,
            )
            bot_updated = True
        except Exception as e:
            logger.warning(
                f"ADMIN_FORCE_SUCCESS: failed to update bot bot_id={bot_id}: {e}"
            )

        return devices_updated, bot_updated
