"""Admin service for test/development utilities.

Provides business logic for forcing entity states to success,
bypassing normal state machine transitions.
"""

from __future__ import annotations

from secbaas.community.api.publish_manage import (
    ForceSuccessResult,
    PublishAdminService,
    PublishNotFoundError,
    UpdateDeviceStatusResult,
)
from secbaas.community.core.repository.bot import BotRepository
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
    ) -> None:
        self._publish_repo = publish_repo
        self._batch_repo = batch_repo
        self._record_repo = record_repo
        self._device_repo = device_repo
        self._bot_repo = bot_repo

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
