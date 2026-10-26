"""BotLifecycle responsibilities for the composed service."""

import asyncio

from secbaas.community.api.bot_manage import (
    BotClusterCreate,
    BotConfig,
    BotStatus,
    CreateBotResponse,
    DestroyBotResponse,
    StopBotResponse,
)
from secbaas.community.api.bot_runtime import BotNotFoundError
from secbaas.community.api.publish_manage import (
    PublishConfig,
    PublishStatus,
    PublishType,
)
from secbaas.community.logger import get_logger

from ._deploy_config import resolve_callback_timeout

logger = get_logger("core-service")


class BotLifecycle:
    """Internal mixin; dependencies and sibling methods belong to the host service."""

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
        env = self._get_current_env()
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
            publish_max_retry_times=bot_config.publish_max_retry_times or 0,
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
        env = self._get_current_env()
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

    async def stop_bot(
        self,
        tenant: str,
        bot_uuid: str,
        operator: str,
        request_id: str,
        auto_approve_publish: bool = False,
    ) -> StopBotResponse:
        env = self._get_current_env()
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
