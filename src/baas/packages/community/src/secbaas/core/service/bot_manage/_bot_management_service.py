"""Bot management API service.

Provides business-level API for Bot lifecycle management.
Orchestrates PublishService for create/destroy operations.
All methods are @staticmethod with explicit tenant and env parameters.

ID Convention:
- bot_uuid: Business UUID exposed in API (format: yyyymmdd+random8digits)
- bot_id: Internal database auto-increment ID (int)
- API layer uses bot_uuid, internal service layer converts to bot_id
"""

import asyncio
from typing import Any

from secbaas.api.bot_manage import (
    BotClusterCreate,
    BotConfig,
    BotCrudService,
    BotDeviceStatus,
    BotDeviceStatusResponse,
    BotListResponse,
    BotManageService,
    BotResponse,
    BotStatus,
    CreateBotResponse,
    DestroyBotResponse,
    RestartBotResponse,
    ScaleBotResponse,
    StopBotResponse,
    UpdateBotResponse,
    UpdateDevicesResponse,
)
from secbaas.api.bot_runtime import BotNotFoundError
from secbaas.api.device_manage import DeviceInfo, DeviceListResponse
from secbaas.api.health_check.bot import (
    BotHealthCheckerService as BotHealthCheckerServiceProtocol,
)
from secbaas.api.health_check.bot import (
    PaasDeviceInfo,
)
from secbaas.api.publish_manage import (
    DEFAULT_CALLBACK_TIMEOUT_SECONDS,
    PublishConfig,
    PublishService,
    PublishStatus,
    PublishType,
    RestartScope,
)
from secbaas.core.repository.bot import BotRecord, BotRepository
from secbaas.core.repository.device import DeviceRecord, DeviceRepository
from secbaas.core.repository.system_config import (
    SystemConfigRepository,
)
from secbaas.core.service.config import SystemConfigKey
from secbaas.core.service.device_manage import device_record_to_response
from secbaas.core.utils.env_utils import get_current_env
from secbaas.logger import get_logger

from ._bot_service import bot_record_to_response

logger = get_logger("core-service")


def resolve_callback_timeout(
    user_value: int | None,
    system_config_repo: SystemConfigRepository | None = None,
) -> int:
    """Resolve callback_timeout_seconds with 3-tier priority.

    Priority chain (highest to lowest):
    1. User bot config value
    2. System config value
    3. Code constant (DEFAULT_CALLBACK_TIMEOUT_SECONDS)

    Args:
        user_value: User-specified value from bot config (may be None)
        system_config_repo: Optional system config repository for tier 2 lookup

    Returns:
        Resolved timeout in seconds
    """
    if user_value is not None:
        return user_value
    if system_config_repo is not None:
        try:
            env = get_current_env()
            record = system_config_repo.get_by_env_and_key(
                env, SystemConfigKey.CALLBACK_TIMEOUT_SECONDS
            )
            if record and record.conf_value:
                return int(record.conf_value)
        except Exception:
            logger.warning(
                "Failed to read callback_timeout from system config", exc_info=True
            )
    logger.info(
        "System config repo is not available, use the DEFAULT_CALLBACK_TIMEOUT_SECONDS"
    )
    return DEFAULT_CALLBACK_TIMEOUT_SECONDS


class DefaultBotManagementService(BotManageService):
    """Bot management API service.

    Provides semantic API for Bot lifecycle management.
    Orchestrates PublishService for create/destroy operations.
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        device_repo: DeviceRepository,
        system_config_repo: SystemConfigRepository,
        publish_service: PublishService,
        bot_service: BotCrudService,
        health_checker: BotHealthCheckerServiceProtocol,
    ) -> None:
        self._bot_repo = bot_repo
        self._device_repo = device_repo
        self._system_config_repo = system_config_repo
        self._publish_service = publish_service
        self._bot_service = bot_service
        self._health_checker = health_checker

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

    def _get_bot_record_by_uuid(
        self,
        bot_uuid: str,
        tenant: str,
        status: str | None = None,
    ) -> BotRecord | None:
        """Look up a BotRecord by business bot_uuid.

        Args:
            bot_repo: BotRepository instance
            bot_uuid: Business UUID (format: yyyymmdd+random8digits)
            tenant: Tenant name for isolation
            status: Optional status filter. UK is (tenant, bot_uuid, status, is_deleted).

            Returns:
                BotRecord if found, None otherwise
        """
        env = get_current_env()
        if status:
            return self._bot_repo.get_by_bot_uuid(
                bot_uuid=bot_uuid, tenant=tenant, env=env, status=status
            )
        records = self._bot_repo.list_by_bot_uuid(
            bot_uuid=bot_uuid, tenant=tenant, env=env
        )
        return records[0] if records else None

    def _get_bot_id_from_uuid(
        self,
        bot_uuid: str,
        tenant: str,
        status: str | None = None,
    ) -> int | None:
        """Convert business bot_uuid to internal database bot_id."""
        record = self._get_bot_record_by_uuid(bot_uuid, tenant, status)
        return record.id if record else None

    def _get_operational_bot_record_by_uuid_for_update(
        self,
        bot_uuid: str,
        tenant: str,
    ) -> BotRecord | None:
        env = get_current_env()
        for preferred_status in (
            BotStatus.ACTIVE.value,
            BotStatus.FAILED.value,
            BotStatus.DESTROYING.value,
            BotStatus.STOPPED.value,
        ):
            record = self._bot_repo.get_by_bot_uuid(
                bot_uuid=bot_uuid, tenant=tenant, env=env, status=preferred_status
            )
            if not record:
                continue
            devices = self._device_repo.list_by_bot_id(
                bot_id=record.id, tenant=tenant, env=env
            )
            if devices:
                return record
        return None

    async def create_bot(
        self,
        tenant: str,
        name: str,
        template_uuid: str,
        device_count: int,
        operator: str,
        request_id: str,
        description: str | None = None,
        config: BotConfig | None = None,
    ) -> CreateBotResponse:
        """Create Bot through publish workflow.

        Creates a new Bot with specified device count through the publish workflow.
        Per D-03: Explicit tenant and env parameters
        Per D-04: Flat parameter structure
        Per D-07: Delegates to PublishService for orchestration

        Args:
            tenant: Tenant name for isolation
            name: Bot name
            template_uuid: Device template UUID for device creation
            device_count: Number of devices to create
            operator: User creating the bot
            request_id: Request ID for correlation (client-provided, required)
            description: Optional bot description
            config: Bot configuration (entity_id, entity_type, deploy_config)

        Returns:
            CreateBotResponse for the created bot with publish_id for workflow tracking
        """
        env = get_current_env()
        logger.info(
            f"Creating bot: tenant={tenant}, env={env}, name={name}, template_uuid={template_uuid}, "
            f"device_count={device_count}, request_id={request_id}"
        )

        # First create bot with minimum device via BotService
        # Additional devices are created via publish workflow
        bot_config = config or BotConfig()
        bot_data = BotClusterCreate(
            bot_name=name,
            bot_desc=description,
            template_uuid=template_uuid,
            device_count=device_count,  # Create all devices upfront
            env=env,
            operator=operator,
            config=bot_config,
        )

        bot = await self._bot_service.create_bot(
            tenant=tenant,
            data=bot_data,
        )
        logger.info(
            f"[create_bot] bot created: bot_id={bot.id} bot_uuid={bot.bot_uuid} "
            f"status={bot.status} device_count={device_count}"
        )

        # Create publish via PublishService for device creation
        publish_config = PublishConfig(
            bot_name=name,
            replica_desired=device_count,
            batch_capacity=min(5, device_count),
            cooldown_seconds=0,
            deploy_config=bot_config.deploy_config,
            callback_timeout_seconds=resolve_callback_timeout(
                bot_config.callback_timeout_seconds, self._system_config_repo
            ),
            auto_approve=bot_config.auto_approve_publish,
        )
        publish = await self._publish_service.create_publish(
            tenant=tenant,
            bot_id=bot.id,
            publish_type=PublishType.CREATE,
            operator=operator,
            request_id=request_id,
            config=publish_config,
        )
        logger.info(
            f"[create_bot] publish created: publish_id={publish.id} "
            f"publish_type={PublishType.CREATE.value} publish_config={publish_config}"
        )

        # Auto-approve publish stage gates when requested
        if bot_config.auto_approve_publish:
            logger.info(
                f"[create_bot] auto_approve_publish=True, "
                f"starting auto-approval loop for publish_id={publish.id}"
            )
            await self._auto_approve_publish(tenant, publish.id, operator)

        # Return the bot with current status and publish_id for workflow tracking
        # Use the bot returned from create_bot directly (avoids querying for ACTIVE status only)
        return CreateBotResponse(
            **bot.model_dump(), publish_id=publish.id, request_id=request_id
        )

    async def _auto_approve_publish(
        self,
        tenant: str,
        publish_id: int,
        operator: str,
        max_iterations: int = 20,
        sleep_seconds: float = 1.0,
    ) -> None:
        """Auto-approve publish stage gates until non-approvable or max iterations.

        Calls approve_stage in a loop, which handles PENDING→ACTIVE and
        APPROVING→ACTIVE transitions. When the publish is ACTIVE (async
        execution in progress), sleeps briefly to allow stage completion.
        Exits when the publish reaches a non-approvable state or the
        iteration budget is exhausted.
        """
        asyncio.create_task(
            self._auto_approve_publish_impl(
                tenant=tenant,
                publish_id=publish_id,
                operator=operator,
                max_iterations=max_iterations,
                sleep_seconds=sleep_seconds,
            )
        )

    async def _auto_approve_publish_impl(
        self,
        tenant: str,
        publish_id: int,
        operator: str,
        max_iterations: int = 20,
        sleep_seconds: float = 1.0,
    ) -> None:
        try:
            for i in range(max_iterations):
                publish = await self._publish_service.get_publish(tenant, publish_id)
                if publish is None:
                    logger.warning(
                        f"[auto_approve] publish_id={publish_id} not found, stopping"
                    )
                    return

                status = publish.status

                if status == PublishStatus.SUCCESS.value:
                    logger.info(
                        f"[auto_approve] publish_id={publish_id} already SUCCESS, done"
                    )
                    return

                if status == PublishStatus.ACTIVE.value:
                    logger.debug(
                        f"[auto_approve] publish_id={publish_id} ACTIVE, "
                        f"waiting for stage execution "
                        f"(iteration {i + 1}/{max_iterations})"
                    )
                    await asyncio.sleep(sleep_seconds)
                    continue

                if status in (
                    PublishStatus.PENDING.value,
                    PublishStatus.APPROVING.value,
                ):
                    logger.info(
                        f"[auto_approve] publish_id={publish_id} status={status}, "
                        f"calling approve_stage (iteration {i + 1}/{max_iterations})"
                    )
                    await self._publish_service.approve_stage(
                        tenant=tenant,
                        publish_id=publish_id,
                        operator=operator,
                        _called_internally=True,
                    )
                    continue

                logger.info(
                    f"[auto_approve] publish_id={publish_id} status={status}, "
                    f"non-approvable terminal state, stopping"
                )
                return

            logger.warning(
                f"[auto_approve] publish_id={publish_id} loop exhausted "
                f"max_iterations={max_iterations}, "
                f"publish continues via callback pathway"
            )
        except Exception:
            logger.exception(
                f"[auto_approve] publish_id={publish_id} background auto-approve failed"
            )

    async def destroy_bot(
        self,
        tenant: str,
        bot_uuid: str,
        operator: str,
        request_id: str,
        auto_approve_publish: bool = False,
    ) -> DestroyBotResponse | None:
        """Destroy Bot through publish workflow.

        Destroys a Bot by creating a DESTROY publish that orchestrates
        graceful device drain and cleanup.

        Per D-03: Explicit tenant and env parameters
        Per D-07: Delegates to PublishService for orchestration

        Args:
            tenant: Tenant name for isolation
            env: Environment for isolation
            bot_uuid: Bot UUID to destroy (business UUID, not internal id)
            operator: User performing the destroy operation
            request_id: Request ID for correlation (client-provided, required)

        Returns:
            DestroyBotResponse with bot info and publish_id, None if bot not found

        Raises:
            ValueError: If bot is already being destroyed (DESTROYING status)
        """
        env = get_current_env()
        logger.info(
            f"Destroying bot: tenant={tenant}, env={env}, bot_uuid={bot_uuid}, operator={operator}"
        )

        # Get bot first to return full info
        bot = await self.get_bot(tenant, bot_uuid)
        if bot is None:
            logger.info(f"Bot not found: {bot_uuid}")
            return None

        logger.info(
            f"[destroy_bot] bot_uuid={bot_uuid} bot_id={bot.id} status={bot.status} "
            f"request_id={request_id}"
        )

        # Check if bot is already being destroyed
        if bot.status == BotStatus.DESTROYING.value:
            raise ValueError("Bot is already being destroyed")

        # Create DESTROY publish via PublishService
        destroy_config = PublishConfig(
            reason="bot_destroy", auto_approve=auto_approve_publish
        )
        publish = await self._publish_service.create_publish(
            tenant=tenant,
            bot_id=bot.id,
            publish_type=PublishType.DESTROY,
            operator=operator,
            request_id=request_id,
            config=destroy_config,
        )

        # Set bot status to DESTROYING immediately after publish creation
        self._bot_repo.update_status(
            bot_id=bot.id,
            tenant=tenant,
            env=env,
            status=BotStatus.DESTROYING.value,
            modifier=operator,
        )
        logger.info(
            f"[destroy_bot] status → DESTROYING: bot_id={bot.id} "
            f"publish_id={publish.id}"
        )

        # Auto-approve publish stage gates when requested
        if auto_approve_publish:
            logger.info(
                f"[destroy_bot] auto_approve_publish=True, "
                f"starting auto-approval loop for publish_id={publish.id}"
            )
            await self._auto_approve_publish(tenant, publish.id, operator)

        # Refresh bot info to include new status (reuse known bot_id to skip UUID resolution)
        refreshed_bot = await self._bot_service.get_bot(tenant=tenant, bot_id=bot.id)
        if refreshed_bot is None:
            raise RuntimeError(f"Bot not found after status update: bot_id={bot.id}")

        logger.info(f"Created DESTROY publish: id={publish.id}")
        return DestroyBotResponse(
            **refreshed_bot.model_dump(), publish_id=publish.id, request_id=request_id
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
        env = get_current_env()
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
        env = get_current_env()
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

    async def scale_bot(
        self,
        tenant: str,
        bot_uuid: str,
        target_count: int,
        operator: str,
        request_id: str,
        auto_approve_publish: bool = False,
    ) -> ScaleBotResponse:
        """Scale Bot to target device count (SCALE_UP or SCALE_DOWN).

        Creates appropriate publish for capacity adjustment:
        - target_count > current: SCALE_UP adding devices
        - target_count < current: SCALE_DOWN removing devices

        Per D-03: Explicit tenant and env parameters
        Per D-04: Flat structure (not nested config)
        Per D-07: Delegates to PublishService

        Args:
            tenant: Tenant name for isolation
            env: Environment for isolation
            bot_uuid: Bot UUID to scale (business UUID, not internal id)
            target_count: Desired number of devices (must be >= 1)
            operator: User performing the scaling operation
            auto_approve_publish: When True, auto-approve all publish stage gates
                                  without manual intervention (default: False)

        Returns:
            ScaleBotResponse with bot info, target_count and publish_id

        Raises:
            ValueError: If bot not found, target_count < 1,
                        or target_count equals current count,
                        or concurrent publish already exists.
        """
        env = get_current_env()
        logger.info(
            f"Scaling bot: tenant={tenant}, env={env}, bot_uuid={bot_uuid}, target={target_count}"
        )

        # Get bot first to return full info
        bot = await self.get_bot(tenant, bot_uuid)
        if bot is None:
            raise BotNotFoundError(bot_uuid)

        logger.info(
            f"[scale_bot] bot_uuid={bot_uuid} bot_id={bot.id} target={target_count}"
        )

        # Check if bot is being destroyed
        if bot.status == BotStatus.DESTROYING.value:
            raise ValueError("Cannot scale bot in DESTROYING status")

        # Validate target_count >= 1
        if target_count < 1:
            raise ValueError(f"Target count must be at least 1, got {target_count}")

        # Get current device count
        device_repo = self._device_repo
        devices = device_repo.list_by_bot_id(bot_id=bot.id, tenant=tenant, env=env)
        current_count = len(devices)
        logger.info(
            f"[scale_bot] bot_id={bot.id} current_count={current_count} "
            f"target_count={target_count} delta={target_count - current_count}"
        )

        # Validate target_count != current
        if target_count == current_count:
            raise ValueError(
                f"Target count equals current count ({current_count}), no scaling needed"
            )

        # Determine publish type
        if target_count > current_count:
            publish_type = PublishType.SCALE_UP
        else:
            publish_type = PublishType.SCALE_DOWN

        # Create publish via PublishService
        scale_amount = abs(target_count - current_count)
        scale_config = PublishConfig(
            replica_desired=target_count,
            batch_capacity=min(10, scale_amount),
            auto_approve=auto_approve_publish,
        )
        publish = await self._publish_service.create_publish(
            tenant=tenant,
            bot_id=bot.id,
            publish_type=publish_type,
            operator=operator,
            request_id=request_id,
            config=scale_config,
        )

        logger.info(
            f"[scale_bot] publish created: publish_id={publish.id} "
            f"type={publish_type.value} target={target_count} current={current_count} config={scale_config}"
        )

        # Auto-approve publish stage gates when requested
        if auto_approve_publish:
            logger.info(
                f"[scale_bot] auto_approve_publish=True, "
                f"starting auto-approval loop for publish_id={publish.id}"
            )
            await self._auto_approve_publish(tenant, publish.id, operator)

        return ScaleBotResponse(
            **bot.model_dump(),
            target_count=target_count,
            publish_id=publish.id,
            request_id=request_id,
        )

    async def update_bot(
        self,
        tenant: str,
        bot_uuid: str,
        operator: str,
        bot_name: str | None = None,
        bot_desc: str | None = None,
        bot_config: BotConfig | None = None,
        request_id: str | None = None,
    ) -> UpdateBotResponse:
        """Update Bot metadata and config.

        Per D-02: Only actually-used fields are writable.
        Per bot-update-process design: config changes trigger UPDATE publish;
        name/description-only updates remain in-place (no publish).

        Args:
            tenant: Tenant name for isolation
            bot_uuid: Bot UUID to update
            operator: User performing the update
            bot_name: New bot name
            bot_desc: New bot description
            bot_config: Bot configuration update (triggers UPDATE publish)
            request_id: Request ID for publish correlation (required if bot_config provided)

        Returns:
            UpdateBotResponse with publish_id if UPDATE publish was created

        Raises:
            ValueError: If bot not found or config update without request_id
        """
        env = get_current_env()
        logger.info(
            f"Updating bot: tenant={tenant}, env={env}, bot_uuid={bot_uuid}, operator={operator}"
        )

        # Look up bot record by UUID (avoids separate get_by_id)
        record = self._get_operational_bot_record_by_uuid_for_update(bot_uuid, tenant)
        if record is None:
            raise BotNotFoundError(bot_uuid)

        bot_id = record.id
        bot_repo = self._bot_repo
        logger.info(
            f"[update_bot] bot_uuid={bot_uuid} bot_id={bot_id} bot_status={record.status} "
            f"has_config={bot_config is not None} has_name={bot_name is not None}"
        )

        # Check if bot is being destroyed - only allow name/description updates
        if record.status == BotStatus.DESTROYING.value:
            if bot_config is not None:
                raise ValueError(
                    "Cannot update bot config while bot is in DESTROYING status. "
                    "Only name and description updates are allowed."
                )

        # Name/description-only updates: in-place (no publish)
        update_kwargs: dict[str, Any] = {"modifier": operator}
        if bot_name is not None:
            update_kwargs["name"] = bot_name
        if bot_desc is not None:
            update_kwargs["description"] = bot_desc

        if len(update_kwargs) > 1:  # More than just modifier
            bot_repo.update_bot(bot_id=bot_id, tenant=tenant, env=env, **update_kwargs)

        # Config change: create UPDATE publish
        publish_id: int | None = None
        if bot_config is not None:
            if not request_id:
                raise ValueError(
                    "request_id is required when updating bot_config (triggers UPDATE publish)"
                )

            # Merge config with existing
            stored_config = (
                BotConfig.model_validate(record.extra_config)
                if record and record.extra_config
                else BotConfig()
            )
            if bot_config.share_policy is not None:
                stored_config.share_policy = bot_config.share_policy
            if bot_config.deploy_config is not None:
                stored_config.deploy_config = bot_config.deploy_config
            if bot_config.entity_id:
                stored_config.entity_id = bot_config.entity_id
            if bot_config.entity_type:
                stored_config.entity_type = bot_config.entity_type
            if bot_config.sla_grade:
                stored_config.sla_grade = bot_config.sla_grade
            if bot_config.auto_approve_publish is not None:
                stored_config.auto_approve_publish = bot_config.auto_approve_publish

            # Also update name on the current bot if provided
            update_kwargs_name: dict[str, Any] = {"modifier": operator}
            if bot_name is not None:
                update_kwargs_name["name"] = bot_name
            if bot_desc is not None:
                update_kwargs_name["description"] = bot_desc

            device_repo = self._device_repo
            devices = device_repo.list_by_bot_id(bot_id=bot_id, tenant=tenant, env=env)
            device_count = len(devices)
            logger.info(
                f"[update_bot] bot_uuid={bot_uuid} bot_id={bot_id} "
                f"devices_found={device_count} "
                f"device_statuses={[(d.device_uuid[:8] if d.device_uuid else '?', d.status) for d in devices]} "
                f"batch_capacity={min(5, device_count) if device_count > 0 else 5}"
            )

            publish_config = PublishConfig(
                bot_name=bot_name or record.name,
                replica_desired=device_count,
                batch_capacity=min(5, device_count) if device_count > 0 else 5,
                deploy_config=stored_config.deploy_config,
                callback_timeout_seconds=resolve_callback_timeout(
                    stored_config.callback_timeout_seconds, self._system_config_repo
                ),
                auto_approve=stored_config.auto_approve_publish,
            )

            publish = await self._publish_service.create_publish(
                tenant=tenant,
                bot_id=bot_id,
                publish_type=PublishType.UPDATE,
                operator=operator,
                request_id=request_id,
                config=publish_config,
            )
            publish_id = publish.id
            logger.info(
                f"[update_bot] publish created: publish_id={publish_id} "
                f"publish_type={PublishType.UPDATE.value} bot_id={bot_id} "
                f"publish_config={publish_config} "
            )

            # Auto-approve publish stage gates when requested
            if stored_config.auto_approve_publish:
                logger.info(
                    f"[update_bot] auto_approve_publish=True, "
                    f"starting auto-approval loop for publish_id={publish_id}"
                )
                await self._auto_approve_publish(tenant, publish_id, operator)

        # Return updated bot info
        updated_bot = await self._bot_service.get_bot(tenant=tenant, bot_id=bot_id)
        if updated_bot is None:
            raise RuntimeError(f"Bot not found after update: bot_id={bot_id}")

        return UpdateBotResponse(**updated_bot.model_dump(), publish_id=publish_id)

    async def restart_bot(
        self,
        tenant: str,
        bot_uuid: str,
        operator: str,
        request_id: str,
        scope: RestartScope = RestartScope.ALL,
        auto_approve_publish: bool = False,
    ) -> RestartBotResponse:
        """Create RESTART publish for Bot device recycling.

        Triggers rolling restart through pipeline with approval gates.

        Per D-01a: RESTART uses pipeline with approval gate
        Per D-03: Explicit tenant and env parameters
        Per D-07: Delegates to PublishService

        Args:
            tenant: Tenant name for isolation
            env: Environment for isolation
            bot_uuid: Bot UUID to restart (business UUID, not internal id)
            operator: User performing the restart
            request_id: Request ID for correlation (client-provided, required)
            scope: Restart scope - RestartScope.ALL (default) or RestartScope.UNHEALTHY
            auto_approve_publish: When True, auto-approve all publish stage gates
                                  without manual intervention (default: False)

        Returns:
            RestartBotResponse with bot info and publish_id

        Raises:
            ValueError: If bot not found, invalid scope, or concurrent publish exists.
        """
        env = get_current_env()
        logger.info(
            f"Restarting bot: tenant={tenant}, env={env}, bot_uuid={bot_uuid}, operator={operator}, scope={scope}"
        )

        # Get bot first to return full info
        bot = await self.get_bot(tenant, bot_uuid)
        if bot is None:
            raise BotNotFoundError(bot_uuid)

        logger.info(
            f"[restart_bot] bot_uuid={bot_uuid} bot_id={bot.id} "
            f"status={bot.status} scope={scope if not isinstance(scope, RestartScope) else scope.value} request_id={request_id}"
        )

        # Validate scope is a RestartScope enum
        if not isinstance(scope, RestartScope):
            raise ValueError(f"Invalid scope: {scope}")

        # Check if bot is being destroyed
        if bot.status == BotStatus.DESTROYING.value:
            raise ValueError("Cannot restart bot in DESTROYING status")

        # Build config via PublishConfig
        restart_config = PublishConfig(
            restart_scope=scope,
            restart_reason="user_initiated",
            auto_approve=auto_approve_publish,
        )

        # Create publish via PublishService
        publish = await self._publish_service.create_publish(
            tenant=tenant,
            bot_id=bot.id,
            publish_type=PublishType.RESTART,
            operator=operator,
            request_id=request_id,
            config=restart_config,
        )

        logger.info(
            f"[restart_bot] publish created: publish_id={publish.id} "
            f"scope={scope.value} bot_id={bot.id} config={restart_config}"
        )

        # Auto-approve publish stage gates when requested
        if auto_approve_publish:
            logger.info(
                f"[restart_bot] auto_approve_publish=True, "
                f"starting auto-approval loop for publish_id={publish.id}"
            )
            await self._auto_approve_publish(tenant, publish.id, operator)

        return RestartBotResponse(
            **bot.model_dump(), publish_id=publish.id, request_id=request_id
        )

    async def update_devices(
        self,
        tenant: str,
        bot_uuid: str,
        operator: str,
        request_id: str | None,
        device_uuids: list[str],
        auto_approve_publish: bool = False,
        config: BotConfig | None = None,
    ) -> UpdateDevicesResponse:
        """Create UPDATE_DEVICE publish for targeted device update.

        Validates device UUIDs (existence + bot ownership), then creates a
        publish scoped to those devices. Bot record status is unchanged.

        When ``config`` is provided, it is merged with the bot's existing
        configuration and persisted before the publish is created, so the
        new devices start with the updated config.

        Args:
            tenant: Tenant name for isolation
            bot_uuid: Bot UUID (business UUID, not internal id)
            operator: User performing the update
            request_id: Request ID for correlation (required when config is provided)
            device_uuids: List of device UUIDs to update (must belong to bot)
            auto_approve_publish: When True, auto-approve all publish stage gates
                                  without manual intervention (default: False)
            config: Optional bot configuration update to merge and persist

        Returns:
            UpdateDevicesResponse with bot info and publish_id

        Raises:
            BotNotFoundError: If bot not found
            ValueError: If devices are invalid, concurrent publish exists,
                        or config update without request_id
        """
        env = get_current_env()
        logger.info(
            f"Updating devices: tenant={tenant}, env={env}, bot_uuid={bot_uuid}, "
            f"operator={operator}, device_uuids={device_uuids}"
        )

        # Get bot first to return full info
        bot = await self.get_bot(tenant, bot_uuid)
        if bot is None:
            raise BotNotFoundError(bot_uuid)

        # Deduplicate device_uuids while preserving order
        unique_device_uuids = list(dict.fromkeys(device_uuids))
        if not unique_device_uuids:
            raise ValueError("device_uuids must contain at least one valid UUID")

        # Fetch all devices attached to this bot
        bot_devices = self._device_repo.list_by_bot_id(
            bot_id=bot.id, tenant=tenant, env=env
        )
        bot_device_map = {d.device_uuid: d for d in bot_devices}

        # Validate each requested device exists and belongs to this bot
        invalid_uuids = []
        for uuid in unique_device_uuids:
            if uuid not in bot_device_map:
                invalid_uuids.append(uuid)

        if invalid_uuids:
            raise ValueError(
                f"Device(s) not found or not belonging to bot {bot_uuid}: {invalid_uuids}"
            )

        logger.info(
            f"[update_devices] bot_uuid={bot_uuid} bot_id={bot.id} "
            f"status={bot.status} devices={len(unique_device_uuids)} request_id={request_id}"
        )

        # Merge and persist config if provided
        bot_repo = self._bot_repo
        if config is not None:
            if not request_id:
                raise ValueError(
                    "request_id is required when updating config (triggers publish)"
                )

            # Merge with existing config
            record = self._bot_repo.get_by_bot_uuid(
                uuid=bot_uuid, tenant=tenant, env=env
            )
            stored_config = (
                BotConfig.model_validate(record.extra_config)
                if record and record.extra_config
                else BotConfig()
            )
            if config.share_policy is not None:
                stored_config.share_policy = config.share_policy
            if config.deploy_config is not None:
                stored_config.deploy_config = config.deploy_config
            if config.entity_id:
                stored_config.entity_id = config.entity_id
            if config.entity_type:
                stored_config.entity_type = config.entity_type
            if config.sla_grade:
                stored_config.sla_grade = config.sla_grade
            if config.auto_approve_publish is not None:
                stored_config.auto_approve_publish = config.auto_approve_publish

            # Persist merged config to bot record
            bot_repo.update_bot(
                bot_id=bot.id,
                tenant=tenant,
                env=env,
                modifier=operator,
                extra_config=stored_config.model_dump(),
            )

            # Build PublishConfig with merged config
            publish_config = PublishConfig(
                auto_approve=auto_approve_publish,
                replica_desired=len(unique_device_uuids),
                target_device_uuids=unique_device_uuids,
                deploy_config=stored_config.deploy_config,
                callback_timeout_seconds=resolve_callback_timeout(
                    stored_config.callback_timeout_seconds, self._system_config_repo
                ),
            )
        else:
            # No config change — use existing bot config for device records
            record = self._get_bot_record_by_uuid(bot_uuid, tenant)
            existing_config = (
                BotConfig.model_validate(record.extra_config)
                if record and record.extra_config
                else BotConfig()
            )
            publish_config = PublishConfig(
                auto_approve=auto_approve_publish,
                replica_desired=len(unique_device_uuids),
                target_device_uuids=unique_device_uuids,
                deploy_config=existing_config.deploy_config,
                callback_timeout_seconds=resolve_callback_timeout(
                    existing_config.callback_timeout_seconds, self._system_config_repo
                ),
            )

        # Create publish via PublishService
        publish = await self._publish_service.create_publish(
            tenant=tenant,
            bot_id=bot.id,
            publish_type=PublishType.UPDATE_DEVICE,
            operator=operator,
            request_id=request_id or "",
            config=publish_config,
        )

        logger.info(
            f"[update_devices] publish created: publish_id={publish.id} "
            f"bot_id={bot.id} devices={len(unique_device_uuids)}"
        )

        # Auto-approve publish stage gates when requested
        if auto_approve_publish:
            logger.info(
                f"[update_devices] auto_approve_publish=True, "
                f"starting auto-approval loop for publish_id={publish.id}"
            )
            await self._auto_approve_publish(tenant, publish.id, operator)

        return UpdateDevicesResponse(
            **bot.model_dump(), publish_id=publish.id, request_id=request_id or ""
        )

    async def stop_bot(
        self,
        tenant: str,
        bot_uuid: str,
        operator: str,
        request_id: str,
        auto_approve_publish: bool = False,
    ) -> StopBotResponse:
        env = get_current_env()
        logger.info(
            f"Stopping bot: tenant={tenant}, env={env}, bot_uuid={bot_uuid}, operator={operator}"
        )

        bot = await self.get_bot(tenant, bot_uuid)
        if bot is None:
            raise BotNotFoundError(bot_uuid)

        logger.info(
            f"[stop_bot] bot_uuid={bot_uuid} bot_id={bot.id} status={bot.status} "
            f"request_id={request_id}"
        )

        # Validate bot is in a stop-able state
        if bot.status in (
            BotStatus.STOPPED.value,
            BotStatus.STOPPING.value,
            BotStatus.DESTROYING.value,
            BotStatus.RELEASED.value,
            BotStatus.PENDING.value,
        ):
            raise ValueError(
                f"Cannot stop bot in {bot.status} status. "
                f"Only ACTIVE and FAILED bots can be stopped."
            )

        # Create STOP publish via PublishService
        stop_config = PublishConfig(
            reason="bot_stop", auto_approve=auto_approve_publish
        )
        publish = await self._publish_service.create_publish(
            tenant=tenant,
            bot_id=bot.id,
            publish_type=PublishType.STOP,
            operator=operator,
            request_id=request_id,
            config=stop_config,
        )

        # Set bot status to STOPPING immediately after publish creation
        self._bot_repo.update_status(
            bot_id=bot.id,
            tenant=tenant,
            env=env,
            status=BotStatus.STOPPING.value,
            modifier=operator,
        )
        logger.info(
            f"[stop_bot] status → STOPPING: bot_id={bot.id} publish_id={publish.id}"
        )

        # Auto-approve publish stage gates when requested
        if auto_approve_publish:
            logger.info(
                f"[stop_bot] auto_approve_publish=True, "
                f"starting auto-approval loop for publish_id={publish.id}"
            )
            await self._auto_approve_publish(tenant, publish.id, operator)

        # Refresh bot info to include new status
        refreshed_bot = await self._bot_service.get_bot(tenant=tenant, bot_id=bot.id)
        if refreshed_bot is None:
            raise RuntimeError(f"Bot not found after status update: bot_id={bot.id}")

        logger.info(f"Created STOP publish: id={publish.id}")
        return StopBotResponse(
            **refreshed_bot.model_dump(), publish_id=publish.id, request_id=request_id
        )

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
        env = get_current_env()
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
        env = get_current_env()
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
        env = get_current_env()
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
        env = get_current_env()
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
        env = get_current_env()
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
        env = get_current_env()
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
