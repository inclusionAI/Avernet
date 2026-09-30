"""DefaultBotFetchStartProgressDispatcher — fetch container startup progress.

Extends BaseDispatcher to dispatch a fetch_start_progress query to a bot's
active device. Only supported on LOCAL platform.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from secbaas.community.api.bot_manage import BotStartProgressResponse
from secbaas.community.api.bot_runtime import (
    BotFetchStartProgressDispatcher,
    BotNotFoundError,
    NoActiveDevicesError,
    NoDevicesFoundError,
)
from secbaas.community.core.service.paas import PaasServiceFacade
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

if TYPE_CHECKING:
    from secbaas.community.core.repository.bot import BotRepository
    from secbaas.community.core.repository.device import DeviceRepository

from ._base_dispatcher import (
    BotBaseDispatcher,
)
from ._device_selector import (
    select_available_device,
)

logger = get_logger("core-service")


class DefaultBotFetchStartProgressDispatcher(
    BotBaseDispatcher, BotFetchStartProgressDispatcher
):
    """Fetch container startup progress from a bot's active device.

    Resolves bot_uuid to paas_device_id and delegates fetch_start_progress
    to the PaaS layer. Only LOCAL platform supports this operation;
    non-LOCAL platforms raise DeviceFacadeException with PLATFORM_ERROR.

    Inherits __init__ and _resolve_bot_device from BotBaseDispatcher.
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        device_repo: DeviceRepository,
        paas_facade: PaasServiceFacade,
    ):
        super().__init__(bot_repo, device_repo, paas_facade)

    async def dispatch_bot_fetch_start_progress(
        self,
        bot_uuid: str,
        tenant: str,
        device_affinity: str | None = None,
    ) -> BotStartProgressResponse:
        """Fetch the startup progress for a bot's device with relaxed status filter.

        Only supported on LOCAL platform. Non-LOCAL platforms raise
        DeviceFacadeException with PLATFORM_ERROR.

        Overrides ``BotBaseDispatcher._resolve_bot_device()`` to accept
        bots in ``ACTIVE``, ``PENDING``, or ``UPDATING`` status (per D-03)
        and devices in ``ACTIVE``, ``PENDING``, ``UPDATING``, or ``OFFLINE`` status
        (via :func:`select_available_device`, per D-02). This allows the
        start-progress API to work during bot creation and publish flows
        where both bot and device are in PENDING state.

        Args:
            bot_uuid: Bot UUID to look up (all statuses via list_by_bot_uuid).
            tenant: Tenant for isolation.
            device_affinity: Optional affinity key for sticky device selection
                (passed to :func:`select_available_device`).

        Returns:
            BotStartProgressResponse with progress data from the PaaS layer.

        Raises:
            BotNotFoundError: Bot not found or in excluded status
                (RELEASED, FAILED).
            NoDevicesFoundError: Bot has no associated devices.
            NoActiveDevicesError: No devices in accepted statuses
                (ACTIVE, PENDING, UPDATING, OFFLINE).
            RuntimeError: Selected device has no provider_device_id.
        """
        env = get_current_env()
        logger.info(
            f"Dispatching bot fetch_start_progress: bot_uuid={bot_uuid}, "
            f"tenant={tenant}"
        )

        # D-01: Bot lookup — list_by_bot_uuid (all statuses) + Python filter
        bots = self._bot_repo.list_by_bot_uuid(bot_uuid, tenant, env)
        if not bots:
            logger.warning(f"Bot not found: bot_uuid={bot_uuid}, tenant={tenant}")
            raise BotNotFoundError(bot_id=bot_uuid)

        # Sort by id descending, pick latest
        bots.sort(key=lambda b: b.id, reverse=True)
        bot = bots[0]

        # D-03: Apply status whitelist (ACTIVE, PENDING, UPDATING)
        if bot.status not in ("ACTIVE", "PENDING", "UPDATING"):
            logger.warning(
                f"Bot not found (excluded status): bot_uuid={bot_uuid}, "
                f"status={bot.status}, tenant={tenant}"
            )
            raise BotNotFoundError(
                bot_id=bot_uuid,
                bot_status=bot.status,
            )

        logger.info(
            f"Resolved bot: bot_uuid={bot_uuid}, bot_id={bot.id}, status={bot.status}"
        )

        # 2. Get devices associated with bot
        devices = self._device_repo.list_by_bot_id(
            bot_id=bot.id, tenant=tenant, env=env
        )
        if not devices:
            logger.warning(f"No devices found for bot: bot_uuid={bot_uuid}")
            raise NoDevicesFoundError(bot_uuid=bot_uuid)

        logger.info(f"Found {len(devices)} devices for bot {bot_uuid}")

        # D-02: Select an available device (ACTIVE/PENDING/UPDATING/OFFLINE)
        selection_method = (
            "consistent_hashing" if device_affinity is not None else "random"
        )
        logger.info(
            f"Selecting device: bot_uuid={bot_uuid}, "
            f"method={selection_method}, "
            f"device_affinity={device_affinity!r}"
        )
        device = select_available_device(devices, device_affinity=device_affinity)
        if not device:
            logger.warning(
                f"No available devices for bot: bot_uuid={bot_uuid}, "
                f"device_statuses={[d.status for d in devices]}"
            )
            raise NoActiveDevicesError(bot_uuid=bot_uuid)

        logger.info(
            f"Selected device: device_uuid={device.device_uuid}, "
            f"provider_device_id={device.provider_device_id}, status={device.status}"
        )

        # 4. Validate provider_device_id
        paas_device_id = device.provider_device_id
        if paas_device_id is None:
            raise RuntimeError(f"Device {device.device_uuid} has no provider_device_id")

        logger.info(f"Using paas_device_id for fetch_start_progress: {paas_device_id}")

        result = await self._paas_facade.fetch_start_progress(
            paas_device_id=paas_device_id,
        )

        logger.info(
            f"Fetch start progress completed: bot_uuid={bot_uuid}, "
            f"paas_device_id={paas_device_id}, "
            f"progress={result.progress}"
        )

        return BotStartProgressResponse(**result.model_dump())
