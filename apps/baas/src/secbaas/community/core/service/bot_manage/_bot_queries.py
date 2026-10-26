"""BotQueries responsibilities for the composed service."""

import asyncio

from secbaas.community.api.bot_manage import (
    BotDeviceStatus,
    BotDeviceStatusResponse,
    BotListResponse,
    BotResponse,
    BotStatus,
)
from secbaas.community.api.bot_runtime import BotNotFoundError
from secbaas.community.api.device_manage import (
    DeviceInfo,
    DeviceListResponse,
)
from secbaas.community.api.health_check.bot import (
    PaasDeviceInfo,
)
from secbaas.community.core.repository.bot import BotRecord
from secbaas.community.core.repository.device import DeviceRecord
from secbaas.community.core.service.device_manage import device_record_to_response
from secbaas.community.logger import get_logger

from ._bot_service import bot_record_to_response

logger = get_logger("core-service")


class BotQueries:
    """Internal mixin; dependencies and sibling methods belong to the host service."""

    @staticmethod
    def _device_record_to_info(record: DeviceRecord) -> DeviceInfo:
        """Convert DeviceRecord to lightweight DeviceInfo for bot embed."""
        return DeviceInfo(
            device_uuid=record.device_uuid,
            status=record.status,
            provider_type=record.provider_type,
            provider_device_id=record.provider_device_id,
            gmt_create=record.gmt_create,
            health="unknown",
        )

    async def get_bot(
        self,
        tenant: str,
        bot_uuid: str,
        health_check: bool = False,
        engine_type: str | None = None,
    ) -> BotResponse | None:
        """Get Bot details with current status.

        Retrieves Bot by UUID with real-time status calculation (D-09).
        When health_check=True, performs real-time device health checks
        via the BotHealthCheckerService and populates device health status.

        Args:
            tenant: Tenant name for isolation
            bot_uuid: Bot UUID to retrieve (business UUID, not internal id)
            health_check: When True, perform real-time device health checks
            engine_type: Optional engine override for health check strategy resolution

        Returns:
            BotResponse if found and tenant matches, None otherwise
        """
        logger.info(
            f"Getting bot: tenant={tenant}, bot_uuid={bot_uuid}, "
            f"health_check={health_check}, engine_type={engine_type}"
        )

        record = self._get_bot_record_by_uuid(bot_uuid, tenant)
        if record is None:
            logger.info(f"Bot not found: {bot_uuid}")
            return None

        calculated_status = self._bot_service._calculate_bot_status(record, tenant)
        response = bot_record_to_response(record)
        response.status = calculated_status.value

        # Perform real-time health checks when requested
        if health_check:
            await self._populate_device_health(
                response=response,
                bot_record=record,
                tenant=tenant,
                engine_type=engine_type,
            )

        logger.info(
            f"[get_bot] bot_uuid={bot_uuid} bot_id={record.id} "
            f"db_status={record.status} calculated_status={response.status} "
            f"devices={len(response.devices)}"
        )
        return response

    async def _populate_device_health(
        self,
        response: BotResponse,
        bot_record: BotRecord,
        tenant: str,
        engine_type: str | None = None,
    ) -> None:
        """Populate device health info on the bot response.

        Fetches device records, runs health checks in parallel,
        and sets the health field on each device.
        """
        env = self._get_current_env()
        device_records = self._device_repo.list_by_bot_id(
            bot_id=bot_record.id, tenant=tenant, env=env
        )
        logger.info(
            f"[get_bot._populate_device_health] Found {len(device_records)} device records "
            f"for bot_id={bot_record.id}"
        )

        if not device_records:
            return

        # Build PaasDeviceInfo for health checker
        paas_devices: list[PaasDeviceInfo] = []
        for dr in device_records:
            has_provider_id = bool(dr.provider_device_id)
            logger.info(
                f"[get_bot._populate_device_health] device_record: "
                f"id={dr.id}, device_uuid={dr.device_uuid}, "
                f"provider_device_id={dr.provider_device_id!r}, "
                f"provider_type={dr.provider_type!r}, "
                f"status={dr.status}, "
                f"has_provider_id={has_provider_id}"
            )
            if dr.provider_device_id:
                paas_devices.append(
                    PaasDeviceInfo(
                        paas_device_id=dr.provider_device_id,
                        device_uuid=dr.device_uuid,
                        provider_type=dr.provider_type,
                        status=dr.status,
                        source_table="baas_device",
                        source_table_id=str(dr.id),
                    )
                )

        logger.info(
            f"[get_bot._populate_device_health] {len(paas_devices)}/{len(device_records)} "
            f"devices have provider_device_id, proceeding to health check"
        )

        if not paas_devices:
            logger.warning(
                f"[get_bot._populate_device_health] No devices with provider_device_id found; "
                f"returning {len(device_records)} devices with health=unknown"
            )
            response.devices = [
                self._device_record_to_info(dr) for dr in device_records
            ]
            return

        # Run health checks in parallel with graceful degradation
        tasks = [
            self._health_checker.check_single_device(d, engine_type)
            for d in paas_devices
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Map results to device health
        health_map: dict[str, str] = {}
        for device, result in zip(paas_devices, results):
            if isinstance(result, Exception):
                logger.warning(
                    f"[get_bot] Health check failed for {device.paas_device_id}: {result}"
                )
                health_map[device.device_uuid] = "false"
            elif result is None:
                health_map[device.device_uuid] = "false"
            else:
                _, health_result = result
                health_map[device.device_uuid] = (
                    "true" if health_result.overall_healthy else "false"
                )

        # Populate devices with health status
        response.devices = [self._device_record_to_info(dr) for dr in device_records]
        for device in response.devices:
            device.health = health_map.get(device.device_uuid, "false")

    async def list_bots(
        self,
        tenant: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> BotListResponse:
        """List Bots with pagination.

        Lists Bots for a tenant with optional status filtering.
        Per D-06: page=1, page_size=20, max=100

        Args:
            tenant: Tenant name for isolation
            env: Environment for isolation
            status: Optional status filter (applied to calculated status)
            page: Page number (1-based, default 1)
            page_size: Items per page (default 20, max 100)

        Returns:
            BotListResponse with items, total, page, page_size
        """
        env = self._get_current_env()
        logger.info(
            f"Listing bots: tenant={tenant}, env={env}, status={status}, page={page}"
        )

        # Apply pagination limits per D-06
        if page_size > 100:
            page_size = 100
        if page < 1:
            page = 1

        # Convert status string to BotStatus if provided
        status_enum = None
        if status:
            try:
                if status == BotStatus.PENDING:
                    status_enum = BotStatus.PENDING
                elif status == BotStatus.ACTIVE:
                    status_enum = BotStatus.ACTIVE
                elif status == BotStatus.FAILED:
                    status_enum = BotStatus.FAILED
                elif status == BotStatus.RELEASED:
                    status_enum = BotStatus.RELEASED
            except (ValueError, AttributeError):
                status_enum = None

        # Delegate to BotService
        result = await self._bot_service.list_bots(
            tenant=tenant,
            status=status_enum,
            page=page,
            page_size=page_size,
        )

        logger.info(
            f"Listed bots: total={result.total}, page={result.page}, returned={len(result.items)}"
        )

        return result

    async def get_bot_with_devices(
        self,
        tenant: str,
        bot_id: int,
    ) -> BotResponse | None:
        """Get bot details with associated device list by internal bot_id.

        Like get_bot but populates the `devices` field with DeviceInfo list.
        Uses bot_id (unique) instead of bot_uuid to avoid ambiguity when
        multiple records exist for the same bot_uuid.
        """
        env = self._get_current_env()
        logger.info(f"Getting bot with devices: tenant={tenant}, bot_id={bot_id}")

        bot_repo = self._bot_repo
        record = bot_repo.get_by_id(bot_id, tenant=tenant, env=env)
        if record is None:
            return None

        response = bot_record_to_response(record)
        calculated_status = self._bot_service._calculate_bot_status(record, tenant)
        response.status = calculated_status.value

        # Fetch devices
        device_repo = self._device_repo
        device_records = device_repo.list_by_bot_id(
            bot_id=bot_id, tenant=tenant, env=env
        )
        response.devices = [self._device_record_to_info(dr) for dr in device_records]

        logger.info(
            f"Bot with devices: bot_id={bot_id}, devices={len(response.devices)}"
        )
        return response

    async def list_bots_with_devices(
        self,
        tenant: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> BotListResponse:
        """List bots with associated device lists.

        Like list_bots but populates the `devices` field for each bot.
        """
        env = self._get_current_env()
        logger.info(
            f"Listing bots with devices: tenant={tenant}, env={env}, "
            f"status={status}, page={page}"
        )

        if page_size > 100:
            page_size = 100
        if page < 1:
            page = 1

        status_enum = None
        if status:
            try:
                if status == BotStatus.PENDING:
                    status_enum = BotStatus.PENDING
                elif status == BotStatus.ACTIVE:
                    status_enum = BotStatus.ACTIVE
                elif status == BotStatus.FAILED:
                    status_enum = BotStatus.FAILED
                elif status == BotStatus.RELEASED:
                    status_enum = BotStatus.RELEASED
            except (ValueError, AttributeError):
                status_enum = None

        result = await self._bot_service.list_bots(
            tenant=tenant,
            status=status_enum,
            page=page,
            page_size=page_size,
        )

        # Populate devices for each bot
        device_repo = self._device_repo
        bot_ids = [bot.id for bot in result.items]
        devices_by_bot = device_repo.list_devices_by_bot_ids(
            bot_ids=bot_ids, tenant=tenant, env=env
        )

        for bot in result.items:
            device_records = devices_by_bot.get(bot.id, [])
            bot.devices = [self._device_record_to_info(dr) for dr in device_records]

        logger.info(f"Listed bots with devices: total={result.total}, page={page}")
        return result

    async def list_bots_with_devices_by_uuid(
        self,
        tenant: str,
        bot_uuid: str,
    ) -> list[BotResponse]:
        """List all bot records matching a bot_uuid with devices.

        A bot_uuid may have multiple records (different statuses).
        Returns BotResponse list with devices populated for each.
        """
        env = self._get_current_env()
        logger.info(
            f"Listing bots with devices by uuid: tenant={tenant}, bot_uuid={bot_uuid}"
        )

        bot_repo = self._bot_repo
        records = bot_repo.list_by_bot_uuid(bot_uuid=bot_uuid, tenant=tenant, env=env)
        if not records:
            raise BotNotFoundError(bot_uuid)

        device_repo = self._device_repo
        results: list[BotResponse] = []
        for record in records:
            response = bot_record_to_response(record)
            calculated_status = self._bot_service._calculate_bot_status(record, tenant)
            response.status = calculated_status.value

            device_records = device_repo.list_by_bot_id(
                bot_id=record.id, tenant=tenant, env=env
            )
            response.devices = [
                self._device_record_to_info(dr) for dr in device_records
            ]
            results.append(response)

        logger.info(f"Listed bots by uuid: bot_uuid={bot_uuid}, records={len(results)}")
        return results

    async def list_devices_by_bot_uuid(
        self,
        tenant: str,
        bot_uuid: str,
    ) -> list[DeviceListResponse]:
        """List devices for all bot records matching a bot_uuid.

        A bot_uuid may have multiple records (different statuses).
        Returns a list of DeviceListResponse, one per matching bot record.
        """
        env = self._get_current_env()
        logger.info(
            f"Listing devices by bot_uuid: tenant={tenant}, bot_uuid={bot_uuid}"
        )

        bot_repo = self._bot_repo
        records = bot_repo.list_by_bot_uuid(bot_uuid=bot_uuid, tenant=tenant, env=env)
        if not records:
            raise BotNotFoundError(bot_uuid)

        device_repo = self._device_repo
        results: list[DeviceListResponse] = []
        for record in records:
            all_devices = device_repo.list_by_bot_id(
                bot_id=record.id, tenant=tenant, env=env
            )
            items = [device_record_to_response(dr) for dr in all_devices]
            results.append(
                DeviceListResponse(
                    items=items,
                    total=len(items),
                    page=1,
                    page_size=len(items),
                )
            )
            logger.info(
                f"Devices for bot_id={record.id} (status={record.status}): "
                f"total={len(items)}"
            )

        return results

    async def list_devices_by_bot_id(
        self,
        tenant: str,
        bot_id: int,
        page: int = 1,
        page_size: int = 20,
    ) -> DeviceListResponse:
        """List devices for a bot by unique bot_id with pagination.

        Returns full DeviceResponse records for a given bot.
        """
        env = self._get_current_env()
        logger.info(
            f"Listing devices by bot_id: tenant={tenant}, bot_id={bot_id}, page={page}"
        )

        device_repo = self._device_repo
        all_records = device_repo.list_by_bot_id(bot_id=bot_id, tenant=tenant, env=env)

        # Paginate in-memory
        total = len(all_records)
        start = (page - 1) * page_size
        end = start + page_size
        page_records = all_records[start:end]

        items = [device_record_to_response(dr) for dr in page_records]

        logger.info(
            f"Listed devices for bot: bot_id={bot_id}, total={total}, returned={len(items)}"
        )

        return DeviceListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_bot_device_status(
        self,
        tenant: str,
        bot_uuid: str,
    ) -> BotDeviceStatusResponse:
        """Get aggregate device status for a bot.

        Queries the operational bot record by UUID, fetches all attached
        devices, and computes whether all are online, all offline, or
        a partial mix.  Provides detailed device counts for transparency.

        Args:
            tenant: Tenant name for isolation
            bot_uuid: Bot UUID (business UUID, not internal id)

        Returns:
            BotDeviceStatusResponse with aggregate status and device counts

        Raises:
            BotNotFoundError: If no bot record is found for the UUID
        """
        env = self._get_current_env()
        logger.info(f"Getting bot device status: tenant={tenant}, bot_uuid={bot_uuid}")

        # Look up operational bot record (ACTIVE / FAILED / DESTROYING)
        record = self._get_operational_bot_record_by_uuid_for_update(bot_uuid, tenant)
        if record is None:
            # Fall back to any record so we can still return device info
            record = self._get_bot_record_by_uuid(bot_uuid, tenant)

        if record is None:
            raise BotNotFoundError(bot_uuid)

        # Query all devices attached to this bot
        device_repo = self._device_repo
        devices = device_repo.list_by_bot_id(bot_id=record.id, tenant=tenant, env=env)

        # Compute aggregate status per D4 rules
        device_count = len(devices)
        active_count = 0
        failed_count = 0
        pending_count = 0
        offline_count = 0
        other_count = 0

        for d in devices:
            if d.status == BotStatus.ACTIVE.value:
                active_count += 1
            elif d.status == BotStatus.FAILED.value:
                failed_count += 1
            elif d.status == BotStatus.PENDING.value:
                pending_count += 1
            elif d.status == "OFFLINE":
                offline_count += 1
            else:
                other_count += 1

        if device_count > 0 and active_count == device_count:
            device_status = BotDeviceStatus.ALL_ONLINE.value
        elif active_count > 0:
            device_status = BotDeviceStatus.PARTIAL_ONLINE.value
        else:
            device_status = BotDeviceStatus.ALL_OFFLINE.value

        logger.info(
            f"[get_bot_device_status] bot_uuid={bot_uuid} bot_id={record.id} "
            f"device_status={device_status} "
            f"devices={device_count} active={active_count} failed={failed_count} "
            f"pending={pending_count} offline={offline_count} other={other_count}"
        )

        return BotDeviceStatusResponse(
            bot_uuid=bot_uuid,
            bot_id=record.id,
            bot_status=record.status,
            device_status=device_status,
            device_count=device_count,
            active_count=active_count,
            failed_count=failed_count,
            pending_count=pending_count,
            offline_count=offline_count,
            other_count=other_count,
        )
