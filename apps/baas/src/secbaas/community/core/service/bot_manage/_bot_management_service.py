"""Bot management API service.

Provides business-level API for Bot lifecycle management.
Orchestrates PublishService for create/destroy operations.
All methods are @staticmethod with explicit tenant and env parameters.

ID Convention:
- bot_uuid: Business UUID exposed in API (format: yyyymmdd+random8digits)
- bot_id: Internal database auto-increment ID (int)
- API layer uses bot_uuid, internal service layer converts to bot_id
"""

from typing import Any

from secbaas.community.api.bot_manage import (
    BotConfig,
    BotCrudService,
    BotManageService,
    BotStatus,
    RestartBotResponse,
    ScaleBotResponse,
    UpdateBotResponse,
    UpdateDevicesResponse,
)
from secbaas.community.api.bot_runtime import BotNotFoundError
from secbaas.community.api.device_manage import (
    DeviceStatus,
)
from secbaas.community.api.health_check.bot import (
    BotHealthCheckerService as BotHealthCheckerServiceProtocol,
)
from secbaas.community.api.publish_manage import (
    DEFAULT_CALLBACK_TIMEOUT_SECONDS,
    PublishConfig,
    PublishService,
    PublishType,
    RestartScope,
)
from secbaas.community.core.repository.bot import BotRecord, BotRepository
from secbaas.community.core.repository.device import DeviceRepository
from secbaas.community.core.repository.system_config import (
    SystemConfigRepository,
)
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

from ._bot_lifecycle import BotLifecycle
from ._bot_queries import BotQueries
from ._deploy_config import (
    merge_deploy_config as merge_deploy_config,
)
from ._deploy_config import (
    resolve_callback_timeout as resolve_callback_timeout,
)

logger = get_logger("core-service")


class DefaultBotManagementService(BotQueries, BotLifecycle, BotManageService):
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

    async def scale_bot(
        self,
        tenant: str,
        bot_uuid: str,
        target_count: int,
        operator: str,
        request_id: str,
        auto_approve_publish: bool = False,
        bot_config: BotConfig | None = None,
        device_uuids: list[str] | None = None,
    ) -> ScaleBotResponse:
        """Scale Bot to target device count (SCALE_UP or SCALE_DOWN).

        Creates appropriate publish for capacity adjustment:
        - target_count > current: SCALE_UP adding devices
        - target_count < current: SCALE_DOWN removing devices

        When ``device_uuids`` is provided alongside ``target_count``, the
        operation targets exactly those devices for SCALE_DOWN. In that
        case ``target_count`` must equal
        ``current_count - len(unique_device_uuids)``. ``device_uuids`` is
        rejected on SCALE_UP. An empty list is treated the same as
        ``None`` (count-based scaling).

        When ``bot_config`` is provided, its non-None fields are merged with
        the bot's existing extra_config and consumed by the publish workflow
        (not persisted to DB). This matches the update_devices merge pattern.

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
            bot_config: Optional bot configuration to merge with existing
                        config for the scale publish workflow
            device_uuids: Optional explicit list of device UUIDs to destroy
                          during SCALE_DOWN. When set, target_count must
                          equal current_count - len(device_uuids).

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

        # Get current device count (fetched once, reused for validation)
        device_repo = self._device_repo
        devices = device_repo.list_by_bot_id(bot_id=bot.id, tenant=tenant, env=env)
        current_count = len(devices)

        # Validate device_uuids if provided
        unique_device_uuids: list[str] | None = None
        if device_uuids:
            unique_device_uuids = list(dict.fromkeys(device_uuids))
            if not unique_device_uuids:
                raise ValueError("device_uuids must contain at least one valid UUID")

            bot_device_map = {d.device_uuid: d for d in devices}

            invalid_uuids = [
                uuid for uuid in unique_device_uuids if uuid not in bot_device_map
            ]
            if invalid_uuids:
                raise ValueError(
                    f"Device(s) not found or not belonging to bot {bot_uuid}: "
                    f"{invalid_uuids}"
                )

            non_active_uuids = [
                uuid
                for uuid in unique_device_uuids
                if bot_device_map[uuid].status != DeviceStatus.ACTIVE.value
            ]
            if non_active_uuids:
                raise ValueError(
                    f"Device(s) not ACTIVE, cannot scale down: {non_active_uuids}"
                )

            # target_count is required and must be consistent with device_uuids.
            derived_target = current_count - len(unique_device_uuids)
            if target_count != derived_target:
                raise ValueError(
                    f"target_count ({target_count}) does not match "
                    f"current_count - len(device_uuids) ({derived_target})"
                )

        # Validate target_count >= 1
        if target_count < 1:
            raise ValueError(f"Target count must be at least 1, got {target_count}")

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

        # Reject device_uuids on scale-up
        if publish_type == PublishType.SCALE_UP and unique_device_uuids:
            raise ValueError("device_uuids cannot be combined with SCALE_UP")

        # Resolve config for PublishConfig
        # Pattern matches update_devices merge at lines 1133-1151:
        # read existing extra_config → merge non-None fields from bot_config → build PublishConfig
        record = self._get_bot_record_by_uuid(bot_uuid, tenant)
        existing_config = (
            BotConfig.model_validate(record.extra_config)
            if record and record.extra_config
            else BotConfig()
        )

        existing_config.deploy_config = merge_deploy_config(
            existing_config.deploy_config,
            bot_config.deploy_config if bot_config is not None else None,
        )

        if bot_config is not None:
            # Merge provided config fields into existing (non-None = override)
            if bot_config.share_policy is not None:
                existing_config.share_policy = bot_config.share_policy
            if bot_config.entity_id:
                existing_config.entity_id = bot_config.entity_id
            if bot_config.entity_type:
                existing_config.entity_type = bot_config.entity_type
            if bot_config.sla_grade:
                existing_config.sla_grade = bot_config.sla_grade
            if bot_config.auto_approve_publish is not None:
                existing_config.auto_approve_publish = bot_config.auto_approve_publish
            if bot_config.publish_max_retry_times is not None:
                existing_config.publish_max_retry_times = (
                    bot_config.publish_max_retry_times
                )
            existing_config.callback_timeout_seconds = (
                bot_config.callback_timeout_seconds
            )

        # Create publish via PublishService
        scale_amount = abs(target_count - current_count)
        scale_config = PublishConfig(
            replica_desired=target_count,
            batch_capacity=min(10, scale_amount),
            auto_approve=auto_approve_publish,
            deploy_config=existing_config.deploy_config,
            callback_timeout_seconds=(
                bot_config.callback_timeout_seconds if bot_config is not None else None
            )
            or DEFAULT_CALLBACK_TIMEOUT_SECONDS,
            publish_max_retry_times=existing_config.publish_max_retry_times or 0,
            target_device_uuids=unique_device_uuids,
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
            f"type={publish_type.value} target={target_count} current={current_count} "
            f"target_device_uuids={unique_device_uuids} config={scale_config}"
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
        template_uuid: str | None = None,
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
            template_uuid: Optional template UUID for the target UPDATE record

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
            f"has_config={bot_config is not None} has_name={bot_name is not None} "
            f"has_template={template_uuid is not None}"
        )

        # Check if bot is being destroyed - only allow name/description updates
        if record.status == BotStatus.DESTROYING.value:
            if bot_config is not None:
                raise ValueError(
                    "Cannot update bot config while bot is in DESTROYING status. "
                    "Only name and description updates are allowed."
                )
            if template_uuid is not None:
                raise ValueError(
                    "Cannot update bot template while bot is in DESTROYING status. "
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

        # Config or template change: create UPDATE publish
        publish_id: int | None = None
        if bot_config is not None or template_uuid is not None:
            if not request_id:
                if bot_config is not None:
                    raise ValueError(
                        "request_id is required when updating bot_config "
                        "(triggers UPDATE publish)"
                    )
                raise ValueError(
                    "request_id is required when updating template_uuid "
                    "(triggers UPDATE publish)"
                )

            # Merge config with existing
            stored_config = (
                BotConfig.model_validate(record.extra_config)
                if record and record.extra_config
                else BotConfig()
            )
            stored_config.deploy_config = merge_deploy_config(
                stored_config.deploy_config,
                bot_config.deploy_config if bot_config is not None else None,
            )

            if bot_config is not None:
                if bot_config.share_policy is not None:
                    stored_config.share_policy = bot_config.share_policy
                if bot_config.entity_id:
                    stored_config.entity_id = bot_config.entity_id
                if bot_config.entity_type:
                    stored_config.entity_type = bot_config.entity_type
                if bot_config.sla_grade:
                    stored_config.sla_grade = bot_config.sla_grade
                if bot_config.auto_approve_publish is not None:
                    stored_config.auto_approve_publish = bot_config.auto_approve_publish
                if bot_config.publish_max_retry_times is not None:
                    stored_config.publish_max_retry_times = (
                        bot_config.publish_max_retry_times
                    )
                stored_config.callback_timeout_seconds = (
                    bot_config.callback_timeout_seconds
                )

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
                callback_timeout_seconds=(
                    bot_config.callback_timeout_seconds
                    if bot_config is not None
                    else None
                )
                or DEFAULT_CALLBACK_TIMEOUT_SECONDS,
                auto_approve=stored_config.auto_approve_publish,
                publish_max_retry_times=stored_config.publish_max_retry_times or 0,
                template_uuid=template_uuid,
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
        publish_max_retry_times: int | None = None,
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
            publish_max_retry_times=publish_max_retry_times or 0,
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
            record = self._get_bot_record_by_uuid(
                bot_uuid=bot_uuid,
                tenant=tenant,
            )
            stored_config = (
                BotConfig.model_validate(record.extra_config)
                if record and record.extra_config
                else BotConfig()
            )
            stored_config.deploy_config = merge_deploy_config(
                stored_config.deploy_config,
                config.deploy_config if config is not None else None,
            )

            if config.share_policy is not None:
                stored_config.share_policy = config.share_policy
            if config.entity_id:
                stored_config.entity_id = config.entity_id
            if config.entity_type:
                stored_config.entity_type = config.entity_type
            if config.sla_grade:
                stored_config.sla_grade = config.sla_grade
            if config.auto_approve_publish is not None:
                stored_config.auto_approve_publish = config.auto_approve_publish
            if config.publish_max_retry_times is not None:
                stored_config.publish_max_retry_times = config.publish_max_retry_times
            stored_config.callback_timeout_seconds = config.callback_timeout_seconds

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
                callback_timeout_seconds=config.callback_timeout_seconds
                or DEFAULT_CALLBACK_TIMEOUT_SECONDS,
                publish_max_retry_times=stored_config.publish_max_retry_times or 0,
            )
        else:
            # No config change — use existing bot config for device records
            record = self._get_bot_record_by_uuid(bot_uuid, tenant)
            existing_config = (
                BotConfig.model_validate(record.extra_config)
                if record and record.extra_config
                else BotConfig()
            )
            existing_config.deploy_config = merge_deploy_config(
                existing_config.deploy_config, None
            )
            publish_config = PublishConfig(
                auto_approve=auto_approve_publish,
                replica_desired=len(unique_device_uuids),
                target_device_uuids=unique_device_uuids,
                deploy_config=existing_config.deploy_config,
                callback_timeout_seconds=resolve_callback_timeout(
                    existing_config.callback_timeout_seconds, self._system_config_repo
                ),
                publish_max_retry_times=existing_config.publish_max_retry_times or 0,
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

    @staticmethod
    def _get_current_env() -> str:
        return get_current_env()
