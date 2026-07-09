"""Bot lifecycle management service.

Manages Bot creation, destruction, and load balancing by coordinating
Device clusters through DeviceService.
"""

import random
import uuid

from secbaas.api.bot_manage import (
    BotClusterCreate,
    BotConfig,
    BotCrudService,
    BotListResponse,
    BotResponse,
    BotStatus,
)
from secbaas.api.bot_runtime import BotNotFoundError
from secbaas.api.device_manage import (
    DeviceConfig,
    DeviceCreate,
    DeviceResponse,
    DeviceService,
)
from secbaas.api.template_manage import (
    DeviceTemplateManageService,
    TemplateNotFoundError,
)
from secbaas.core.repository.bot import (
    BotRecord,
    BotRepository,
)
from secbaas.core.repository.bot_device_rel import (
    BotDeviceRelRepository,
)
from secbaas.core.repository.device import (
    DeviceRepository,
)
from secbaas.core.service.device_manage import device_record_to_response
from secbaas.core.utils.env_utils import get_current_env
from secbaas.logger import get_logger

logger = get_logger("core-service")


def bot_record_to_response(
    record: BotRecord | None, devices: list[DeviceResponse] | None = None
) -> BotResponse:
    """Convert BotRecord to BotResponse."""
    if record is None:
        raise RuntimeError("Bot record is None")
    config = BotConfig.model_validate(record.extra_config or {})
    return BotResponse(
        id=record.id,
        bot_uuid=record.bot_uuid,
        tenant=record.tenant,
        env=record.env,
        domain=record.domain,
        is_deleted=record.is_deleted,
        creator=record.creator,
        modifier=record.modifier,
        status=record.status,
        name=record.name,
        description=record.description,
        template_uuid=record.template_uuid,
        replica_desired=record.replica_desired,
        replica_minimum=record.replica_minimum,
        replica_maximum=record.replica_maximum,
        auto_scaling_enabled=record.auto_scaling_enabled,
        sla_grade=record.sla_grade,
        gmt_create=record.gmt_create,
        gmt_modified=record.gmt_modified,
        config=config,
    )


class DefaultBotCrudService(BotCrudService):
    """Bot lifecycle management service."""

    def __init__(
        self,
        bot_repo: BotRepository,
        device_repo: DeviceRepository,
        rel_repo: BotDeviceRelRepository,
        device_template_service: DeviceTemplateManageService,
        device_service: DeviceService,
    ) -> None:
        self._bot_repo = bot_repo
        self._device_repo = device_repo
        self._rel_repo = rel_repo
        self._device_template_service = device_template_service
        self._device_service = device_service

    def resolve_bot_id_from_uuid(self, bot_uuid: str, tenant: str) -> int | None:
        """Resolve a business bot UUID to internal database ID.

        Looks up the single ACTIVE bot record by UUID with tenant+env isolation.
        See BotRepository.get_active_by_bot_uuid for data integrity guarantees.

        Returns:
            Internal bot_id if found, None if no active bot exists.

        Raises:
            RuntimeError: Multiple ACTIVE bots found (data integrity violation).
        """
        env = get_current_env()
        record = self._bot_repo.get_active_by_bot_uuid(
            bot_uuid=bot_uuid, tenant=tenant, env=env
        )
        if record is None:
            return None
        return record.id

    async def create_bot_record(
        self,
        tenant: str,
        source_bot_id: int,
        new_config: BotConfig | None = None,
        new_name: str | None = None,
        operator: str = "system",
    ) -> BotResponse:
        """Create a new bot record by cloning an existing one with PENDING status.

        Used by UPDATE publish flow: creates a new bot record (same bot_uuid,
        new id) with status=PENDING. The old ACTIVE bot remains until
        complete_publish transfers relationships.

        Args:
            tenant: Tenant name for isolation
            source_bot_id: The existing bot record to clone
            new_config: Optional new BotConfig for the cloned record
            new_name: Optional new name for the cloned record
            operator: User performing the operation

        Returns:
            BotResponse for the new PENDING bot record

        Raises:
            BotNotFoundError: If source bot not found
        """
        env = get_current_env()
        logger.info(
            f"Creating bot record from source: tenant={tenant}, source={source_bot_id}, operator={operator}"
        )

        bot_repo = self._bot_repo

        extra_config = None
        if new_config is not None:
            extra_config = new_config.model_dump(exclude_none=True)

        new_bot_id = bot_repo.insert_bot_record(
            source_bot_id=source_bot_id,
            tenant=tenant,
            env=env,
            status=BotStatus.PENDING.value,
            extra_config=extra_config,
            name=new_name,
            modifier=operator,
        )

        record = bot_repo.get_by_id(new_bot_id, tenant, env)
        if record is None:
            raise RuntimeError(f"New bot record not found: {new_bot_id}")

        logger.info(f"Bot record created: id={new_bot_id}, uuid={record.bot_uuid}")
        return bot_record_to_response(record)

    async def create_bot(
        self,
        tenant: str,
        data: BotClusterCreate,
    ) -> BotResponse:
        """
        Create Bot with associated Device cluster.

        Flow per decisions D-01, D-02, D-03:
        1. Create PENDING Bot record
        2. Create Devices sequentially (D-01)
        3. Create Bot-Device relationship for each success (D-03)
        4. Calculate Bot status: ACTIVE if ≥1 success, FAILED if all fail (D-02, D-04)
        """
        env = get_current_env()
        logger.info(
            f"Creating bot: tenant={tenant}, env={env}, name={data.bot_name}, "
            f"devices={data.device_count}"
        )

        bot_repo = self._bot_repo
        rel_repo = self._rel_repo

        # Validate template exists
        template = self._device_template_service.get_online_template_by_uuid(
            tenant, data.template_uuid
        )
        if not template:
            raise TemplateNotFoundError(data.template_uuid)

        # Generate bot_uuid
        bot_uuid = f"BOT-{uuid.uuid4().hex}"

        # Create PENDING Bot record
        bot_config = data.config or BotConfig()
        bot_id = bot_repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=env,
            domain=data.domain,
            creator=data.operator,
            modifier=data.operator,
            status=BotStatus.PENDING.value,
            name=data.bot_name,
            description=data.bot_desc,
            template_uuid=template.template_uuid
            if hasattr(template, "template_uuid")
            else None,
            replica_desired=data.device_count,
            replica_minimum=1,
            replica_maximum=max(data.device_count, 10),
            auto_scaling_enabled=0,
            sla_grade=bot_config.sla_grade,
            extra_config=bot_config.model_dump(),
        )
        logger.info(f"Bot record created: id={bot_id}, uuid={bot_uuid}")

        # Create Devices sequentially (D-01)
        successful_devices: list[DeviceResponse] = []
        failed_count = 0

        for i in range(data.device_count):
            try:
                device_data = DeviceCreate(
                    domain=data.domain,
                    operator=data.operator,
                    extra_config=DeviceConfig(
                        template_uuid=data.template_uuid,
                        deploy_config=bot_config.deploy_config,
                        metadata={
                            "bot_uuid": bot_uuid,
                            "entity_type": bot_config.entity_type,
                            "entity_id": bot_config.entity_id,
                        },
                    ),
                )

                device = self._device_service.create_device(
                    tenant=tenant,
                    data=device_data,
                )
                successful_devices.append(device)
                logger.info(
                    f"Device {i + 1}/{data.device_count} created: {device.device_uuid}"
                )

                # Create Bot-Device relationship immediately (D-03)
                rel_repo.insert_rel(
                    bot_id=bot_id,
                    device_uuid=device.device_uuid,
                    tenant=tenant,
                    env=env,
                    domain=data.domain,
                    creator=data.operator,
                    modifier=data.operator,
                )
                logger.info(
                    f"Bot-Device relationship created: bot={bot_id}, "
                    f"device={device.device_uuid}"
                )

            except Exception as e:
                failed_count += 1
                logger.error(f"Device {i + 1}/{data.device_count} creation failed: {e}")
                # Continue with next device (D-02: best-effort)

        # Devices are PENDING after creation — bot stays PENDING
        # until publish execute stage calls DeviceService.start().
        if failed_count == data.device_count:
            bot_status = BotStatus.FAILED.value
        else:
            bot_status = BotStatus.PENDING.value

        # Update Bot status
        bot_repo.update_status(
            bot_id=bot_id,
            tenant=tenant,
            env=env,
            status=bot_status,
            modifier=data.operator,
        )
        logger.info(
            f"Bot status updated: bot={bot_id}, status={bot_status}, "
            f"devices_success={len(successful_devices)}, devices_failed={failed_count}"
        )

        # Return complete Bot info
        logger.info(
            f"[create_bot] Readback step: bot_id={bot_id}, tenant={tenant}, env={env}"
        )
        record = bot_repo.get_by_id(bot_id, tenant, env)
        if record is None:
            logger.error(
                f"[create_bot] CRITICAL: bot record not found on readback! "
                f"bot_id={bot_id}, tenant={tenant}, env={env}. "
                f"This indicates DB write was lost or routed to a read-only replica."
            )
        return bot_record_to_response(record, successful_devices)

    async def select_device(
        self,
        tenant: str,
        bot_id: int,
    ) -> DeviceResponse:
        """
        Select a random ACTIVE Device from Bot's cluster for load balancing.

        Per D-06, D-07:
        - Only ACTIVE devices are eligible
        - Raises RuntimeError if no ACTIVE devices available
        - No blocking/wait logic (fail-fast)

        Args:
            tenant: Tenant name for isolation
            bot_id: Bot ID whose cluster to select from

        Returns:
            DeviceResponse of randomly selected ACTIVE device

        Raises:
            ValueError: If bot not found
            RuntimeError: If no ACTIVE devices available
        """
        from secbaas.api.device_manage import DeviceStatus

        env = get_current_env()
        logger.info(
            f"Selecting device for bot: tenant={tenant}, bot={bot_id}, env={env}"
        )

        bot_repo = self._bot_repo
        device_repo = self._device_repo

        # Verify bot exists and belongs to tenant+env
        bot = bot_repo.get_by_id(bot_id, tenant, env)
        if not bot:
            raise BotNotFoundError(str(bot_id))

        # Get all devices associated with this bot
        device_records = device_repo.list_by_bot_id(
            bot_id=bot_id, tenant=tenant, env=env
        )
        logger.info(f"Found {len(device_records)} devices for bot {bot_id}")

        # Filter for ACTIVE devices only (D-06)
        active_devices = [
            record
            for record in device_records
            if record.status == DeviceStatus.ACTIVE.value
        ]
        logger.info(f"Found {len(active_devices)} ACTIVE devices for bot {bot_id}")

        # Fail-fast if no ACTIVE devices (D-07)
        if not active_devices:
            raise RuntimeError("No available Device for Bot")

        # Random selection from ACTIVE devices
        selected = random.choice(active_devices)
        logger.info(f"Selected device: {selected.device_uuid} for bot {bot_id}")

        return device_record_to_response(selected)

    def _calculate_bot_status(self, bot: BotRecord, tenant: str) -> BotStatus:
        """
        Calculate Bot status from its associated Devices per D-04, D-05.

        Status rules per D-04:
        - DESTROYING: Return stored status directly (destroy in progress, not calculated)
        - ACTIVE if >=1 associated Device is ACTIVE
        - FAILED if all Devices are FAILED
        - PENDING if no Devices are ACTIVE yet

        Args:
            bot: BotRecord to calculate status for
            tenant: Tenant name for isolation

        Returns:
            Calculated BotStatus based on Device cluster state, or stored status
            for DESTROYING bots
        """
        from secbaas.api.device_manage import DeviceStatus

        env = get_current_env()

        # STORED statuses: DESTROYING, STOPPING, STOPPED — in-progress or terminal, not calculated
        if bot.status in (
            BotStatus.DESTROYING.value,
            BotStatus.STOPPING.value,
            BotStatus.STOPPED.value,
        ):
            return BotStatus(bot.status)

        # Get all devices associated with this bot (D-05: on-demand query)
        device_repo = self._device_repo
        devices = device_repo.list_by_bot_id(bot_id=bot.id, tenant=tenant, env=env)

        if not devices:
            logger.info(
                f"[calculate_status] bot_id={bot.id} bot_uuid={bot.bot_uuid} "
                f"no_devices → stored_status={bot.status}"
            )
            return BotStatus(bot.status)

        # fmt: off
        active_count = sum(
            1 for d in devices if d.status == DeviceStatus.ACTIVE.value
        )
        failed_count = sum(
            1 for d in devices if d.status == DeviceStatus.FAILED.value
        )
        pending_count = sum(
            1 for d in devices if d.status == DeviceStatus.PENDING.value
        )
        # fmt: on
        logger.info(
            f"[calculate_status] bot_id={bot.id} bot_uuid={bot.bot_uuid} "
            f"total={len(devices)} active={active_count} failed={failed_count} "
            f"pending={pending_count}"
        )

        # Apply D-04 rules
        if active_count >= 1:
            return BotStatus.ACTIVE
        elif failed_count == len(devices):
            return BotStatus.FAILED
        else:
            # Mix of PENDING/RELEASED or all PENDING
            return BotStatus.PENDING

    def _calculate_bot_statuses(
        self, records: list[BotRecord], tenant: str
    ) -> dict[int, BotStatus]:
        """Batch-compute bot statuses from a single device query.

        Uses list_devices_by_bot_ids to fetch all devices in one query,
        then computes each bot's status from the pre-fetched device data.

        Args:
            records: List of BotRecord to calculate statuses for
            tenant: Tenant name for isolation

        Returns:
            Dict mapping bot ID to calculated BotStatus
        """
        from secbaas.api.device_manage import DeviceStatus

        if not records:
            return {}

        env = get_current_env()

        # Fetch all devices for all bots in a single query
        device_repo = self._device_repo
        bot_ids = [r.id for r in records]
        devices_by_bot = device_repo.list_devices_by_bot_ids(
            bot_ids=bot_ids, tenant=tenant, env=env
        )
        logger.info(
            f"[calculate_statuses] batch_size={len(bot_ids)} "
            f"total_devices={sum(len(v) for v in devices_by_bot.values())}"
        )

        result: dict[int, BotStatus] = {}
        for record in records:
            # STORED statuses: DESTROYING, STOPPING, STOPPED — in-progress or terminal, not calculated
            if record.status in (
                BotStatus.DESTROYING.value,
                BotStatus.STOPPING.value,
                BotStatus.STOPPED.value,
            ):
                result[record.id] = BotStatus(record.status)
                continue

            devices = devices_by_bot.get(record.id, [])
            if not devices:
                result[record.id] = BotStatus(record.status)
                continue

            active_count = sum(
                1 for d in devices if d.status == DeviceStatus.ACTIVE.value
            )
            failed_count = sum(
                1 for d in devices if d.status == DeviceStatus.FAILED.value
            )

            if active_count >= 1:
                result[record.id] = BotStatus.ACTIVE
            elif failed_count == len(devices):
                result[record.id] = BotStatus.FAILED
            else:
                result[record.id] = BotStatus.PENDING

        return result

    async def get_bot(
        self,
        tenant: str,
        bot_id: int,
        include_status: bool = True,
    ) -> BotResponse | None:
        """
        Get Bot by ID with optional calculated status.

        Per D-05: Status is calculated on-demand from Device cluster state.

        Args:
            tenant: Tenant name for isolation
            bot_id: Bot ID to retrieve
            include_status: If True, calculate status from devices (1 DB query).
                If False, use stored status from record (no extra query).

        Returns:
            BotResponse if found, None otherwise
        """
        env = get_current_env()
        logger.info(f"Getting bot: tenant={tenant}, bot={bot_id}, env={env}")

        bot_repo = self._bot_repo

        record = bot_repo.get_by_id(bot_id, tenant, env)
        if not record:
            logger.info(f"Bot not found: {bot_id}")
            return None

        response = bot_record_to_response(record)

        if include_status:
            calculated_status = self._calculate_bot_status(record, tenant)
            response.status = calculated_status.value

        logger.info(f"Bot retrieved: id={bot_id}, status={response.status}")
        return response

    async def list_bots(
        self,
        tenant: str,
        status: BotStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> "BotListResponse":
        """
        List Bots for a tenant with optional status filtering.

        Per D-05: Status is calculated on-demand for each Bot.

        Args:
            tenant: Tenant name for isolation
            status: Optional status filter (calculated status)
            page: Page number (1-based)
            page_size: Items per page

        Returns:
            BotListResponse with pagination info
        """
        env = get_current_env()
        logger.info(
            f"Listing bots: tenant={tenant}, env={env}, status={status}, page={page}"
        )

        bot_repo = self._bot_repo

        # Get bots from repository (filter by tenant+env)
        total, records = bot_repo.list_bots(
            tenant=tenant,
            env=env,
            page=page,
            page_size=page_size,
        )

        # Convert records to responses with calculated status (batch)
        statuses = self._calculate_bot_statuses(records, tenant)
        items = []
        for record in records:
            calculated_status = statuses[record.id]

            # Apply status filter if specified
            if status is not None and calculated_status != status:
                continue

            response = bot_record_to_response(record)
            response.status = calculated_status.value
            items.append(response)

        # Adjust total for status filter (approximate - full count would need separate query)
        filtered_total = len(items)

        logger.info(
            f"Listed bots: total={total}, filtered={filtered_total}, page={page}"
        )

        return BotListResponse(
            items=items,
            total=filtered_total if status else total,
            page=page,
            page_size=page_size,
        )

    async def destroy_bot(
        self,
        tenant: str,
        bot_id: int,
        modifier: str,
    ) -> bool:
        """
        Destroy Bot and all associated Devices with cascading cleanup.

        Flow per D-08, D-09, D-10:
        1. Mark Bot as RELEASED (indicates destruction in progress)
        2. Destroy Devices sequentially (D-08)
        3. Soft-delete Bot-Device relationship for each destroyed Device (D-10)
        4. Log errors and continue on failures (D-09)

        Args:
            tenant: Tenant name for isolation
            bot_id: Bot ID to destroy
            modifier: User ID performing the destruction

        Returns:
            True if bot was found and destruction initiated, False otherwise
        """
        env = get_current_env()
        logger.info(
            f"Destroying bot: tenant={tenant}, bot={bot_id}, env={env}, modifier={modifier}"
        )

        bot_repo = self._bot_repo
        rel_repo = self._rel_repo
        device_repo = self._device_repo

        # Verify bot exists with tenant+env isolation
        bot = bot_repo.get_by_id(bot_id, tenant, env)
        if not bot:
            logger.warning(f"Bot not found: {bot_id}")
            return False

        # Check if already released
        if bot.status == BotStatus.RELEASED.value:
            logger.info(f"Bot already released: {bot_id}")
            return False

        # Mark Bot as RELEASED immediately (D-09: indicates destruction in progress)
        bot_repo.update_status(
            bot_id=bot_id,
            tenant=tenant,
            env=env,
            status=BotStatus.RELEASED.value,
            modifier=modifier,
        )
        logger.info(f"Bot status updated to RELEASED: {bot_id}")

        # Get all device relationships for this bot
        relationships = rel_repo.list_by_bot_id(bot_id=bot_id, tenant=tenant, env=env)
        logger.info(f"Found {len(relationships)} device relationships for bot {bot_id}")

        # Destroy devices sequentially (D-08)
        success_count = 0
        failure_count = 0

        for rel in relationships:
            try:
                # Get device record by UUID
                device = device_repo.get_active_by_device_uuid(
                    device_uuid=rel.device_uuid,
                    tenant=tenant,
                    env=env,
                )

                if not device:
                    logger.warning(
                        f"Device not found for relationship: {rel.device_uuid}"
                    )
                    # Still clean up the relationship
                    rel_repo.soft_delete(
                        rel_id=rel.id, tenant=tenant, env=env, modifier=modifier
                    )
                    logger.info(f"Orphaned relationship cleaned up: rel_id={rel.id}")
                    continue

                # Destroy the device (D-08: sequential)
                destroy_response = await self._device_service.destroy_device_by_uuid(
                    tenant=tenant,
                    device_uuid=rel.device_uuid,
                    modifier=modifier,
                )

                if destroy_response.success:
                    success_count += 1
                    logger.info(f"Device destroyed: {device.id} ({rel.device_uuid})")

                    # Log any non-fatal warnings from hook
                    if destroy_response.error_message:
                        logger.warning(
                            f"Device destroyed with warnings: {destroy_response.error_message}"
                        )

                    # Soft-delete Bot-Device relationship immediately (D-10)
                    rel_repo.soft_delete(
                        rel_id=rel.id, tenant=tenant, env=env, modifier=modifier
                    )
                    logger.info(
                        f"Bot-Device relationship soft-deleted: rel_id={rel.id}"
                    )
                else:
                    failure_count += 1
                    error_msg = (
                        destroy_response.error_message or "Unknown destruction error"
                    )
                    logger.error(
                        f"Device destruction failed: {device.id}, error: {error_msg}"
                    )
                    # Continue with next device (D-09)

            except Exception as e:
                failure_count += 1
                logger.error(f"Error destroying device for relationship {rel.id}: {e}")
                # Continue with other devices (D-09: best-effort destruction)
                continue

        logger.info(
            f"Bot destruction completed: bot={bot_id}, "
            f"devices_success={success_count}, devices_failed={failure_count}"
        )
        return True
