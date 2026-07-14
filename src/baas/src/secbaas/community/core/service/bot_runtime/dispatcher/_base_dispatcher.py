"""Base Dispatcher.

Provides the shared bot resolution flow (bot lookup -> device listing ->
active device selection -> provider_device_id validation) for the
dispatcher implementations.

Design decisions (per design.md):
- D-01: Strategy pattern via callable delegate, not Protocol subclass
- D-02: Shared flow as private _resolve_bot_device() method
- D-03: Subclass handlers per action in private files
- D-04: Package structure follows core/service/bot_manage/ convention
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import (
    BotNotFoundError,
    NoActiveDevicesError,
    NoDevicesFoundError,
)
from secbaas.community.core.service.paas import PaasServiceFacade
from secbaas.community.logger import get_logger

from ._device_selector import (
    select_active_device,
)

if TYPE_CHECKING:
    from secbaas.community.core.repository.bot import BotRepository
    from secbaas.community.core.repository.device import DeviceRepository

logger = get_logger("core-service")


class BotBaseDispatcher:
    """Base class for dispatcher implementations.

    Provides __init__ and _resolve_bot_device shared by all concrete dispatchers.
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        device_repo: DeviceRepository,
        paas_facade: PaasServiceFacade,
    ):
        """Initialize the service with dependencies.

        Args:
            bot_repo: Repository for bot queries
            device_repo: Repository for device queries
            paas_facade: Facade for PaaS operations
        """
        self._bot_repo = bot_repo
        self._device_repo = device_repo
        self._paas_facade = paas_facade

    async def _resolve_bot_device(
        self,
        bot_uuid: str,
        tenant: str,
        env: str,
        device_affinity: str | None = None,
    ) -> tuple[Any, Any, str]:
        """Shared flow: resolve bot -> devices -> active device.

        This is the core shared logic extracted from the three original
        resolver services. Steps 1-4 of the 5-step resolution flow.

        Args:
            bot_uuid: Bot UUID to look up
            tenant: Tenant for isolation
            env: Environment for isolation
            device_affinity: Optional affinity key for sticky device selection

        Returns:
            Tuple of (bot_record, device_record, paas_device_id)

        Raises:
            BotNotFoundError: Bot not found by UUID
            NoDevicesFoundError: Bot has no associated devices
            NoActiveDevicesError: Bot has no ACTIVE devices
            RuntimeError: Selected device has no provider_device_id
        """
        # 1. Look up bot by UUID
        bot = self._bot_repo.get_active_by_bot_uuid(bot_uuid, tenant, env)
        if not bot:
            logger.warning(f"Bot not found: bot_uuid={bot_uuid}, tenant={tenant}")
            raise BotNotFoundError(bot_uuid)

        # 2. Get devices associated with bot
        devices = self._device_repo.list_by_bot_id(
            bot_id=bot.id, tenant=tenant, env=env
        )
        if not devices:
            logger.warning(f"No devices found for bot: bot_uuid={bot_uuid}")
            raise NoDevicesFoundError(bot_uuid)

        logger.info(f"Found {len(devices)} devices for bot {bot_uuid}")

        # 3. Select an ACTIVE device
        selection_method = (
            "consistent_hashing" if device_affinity is not None else "random"
        )
        logger.info(
            f"Selecting device: bot_uuid={bot_uuid}, "
            f"method={selection_method}, "
            f"device_affinity={device_affinity!r}"
        )
        device = select_active_device(devices, device_affinity=device_affinity)
        if not device:
            logger.warning(
                f"No active devices for bot: bot_uuid={bot_uuid}, "
                f"device_statuses={[d.status for d in devices]}"
            )
            raise NoActiveDevicesError(bot_uuid)

        logger.info(
            f"Selected device: device_uuid={device.device_uuid}, "
            f"provider_device_id={device.provider_device_id}, status={device.status}"
        )

        # 4. Validate provider_device_id
        paas_device_id = device.provider_device_id
        if paas_device_id is None:
            raise RuntimeError(f"Device {device.device_uuid} has no provider_device_id")

        return bot, device, paas_device_id
