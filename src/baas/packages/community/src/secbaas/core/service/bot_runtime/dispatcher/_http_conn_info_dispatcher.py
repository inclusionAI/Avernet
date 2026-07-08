"""DefaultBotHttpConnInfoDispatcher — HTTP connection info resolution.

Extends BaseDispatcher to resolve HTTP connection info for a bot's active device.
Supports both auto-selection (default) and targeted device selection via device_uuid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from secbaas.api.bot_runtime import (
    BotHttpConnInfoDispatcher,
    BotNotFoundError,
    HttpConnectionInfo,
    NoActiveDevicesError,
    NoDevicesFoundError,
)
from secbaas.core.service.paas import PaasServiceFacade
from secbaas.core.utils.env_utils import get_current_env
from secbaas.logger import get_logger

if TYPE_CHECKING:
    from secbaas.core.repository.bot import BotRepository
    from secbaas.core.repository.device import DeviceRepository

from ._base_dispatcher import (
    BotBaseDispatcher,
)

logger = get_logger("core-service")


class DefaultBotHttpConnInfoDispatcher(BotBaseDispatcher, BotHttpConnInfoDispatcher):
    """HTTP connection info resolution for a bot.

    Implements BotHttpConnInfoDispatcher protocol for HTTP connection info resolution.
    Inherits __init__ and _resolve_bot_device from BaseDispatcher.
    Supports both auto-selection (default) and specific device targeting via device_uuid.
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        device_repo: DeviceRepository,
        paas_facade: PaasServiceFacade,
    ):
        super().__init__(bot_repo, device_repo, paas_facade)

    async def dispatch_bot_http_conn_info(
        self,
        bot_uuid: str,
        port: int,
        path: str,
        tenant: str,
        device_affinity: str | None = None,
        device_uuid: str | None = None,
    ) -> HttpConnectionInfo:
        """Dispatch HTTP connection info resolution for a bot.

        When device_uuid is provided, resolves connection for that specific device.
        When device_uuid is absent, auto-selects an active device (existing behavior).
        """
        env = get_current_env()
        if device_uuid is not None:
            return await self._dispatch_for_specific_device(
                bot_uuid=bot_uuid,
                device_uuid=device_uuid,
                tenant=tenant,
                port=port,
                path=path,
            )

        # Auto-select flow (existing behavior)
        logger.info(
            f"Dispatching HTTP conn info: bot_uuid={bot_uuid}, port={port}, path={path}"
        )

        _, _, paas_device_id = await self._resolve_bot_device(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=env,
            device_affinity=device_affinity,
        )

        logger.info(f"Using paas_device_id: {paas_device_id}")

        conn_info = await self._paas_facade.resolve_invoke_http_info(
            paas_device_id=paas_device_id,
            port=port,
            path=path,
        )

        logger.info(f"Dispatched HTTP conn info: http_url={conn_info.http_url}")

        return conn_info

    async def _dispatch_for_specific_device(
        self,
        bot_uuid: str,
        device_uuid: str,
        tenant: str,
        port: int,
        path: str,
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info for a specific device UUID.

        Validates:
        1. Bot exists
        2. Device exists and is associated with the bot
        3. Device is in ACTIVE status

        Raises:
            BotNotFoundError: Bot not found
            NoDevicesFoundError: Device not found or not associated with bot
            NoActiveDevicesError: Device is not in ACTIVE status
        """
        env = get_current_env()
        logger.info(
            f"Resolving HTTP for specific device: bot_uuid={bot_uuid}, "
            f"device_uuid={device_uuid}"
        )

        # 1. Look up bot
        bot = self._bot_repo.get_active_by_bot_uuid(bot_uuid, tenant, env)
        if not bot:
            logger.warning(f"Bot not found: bot_uuid={bot_uuid}, tenant={tenant}")
            raise BotNotFoundError(bot_uuid)

        # 2. List all devices for this bot and find the specific one
        devices = self._device_repo.list_by_bot_id(
            bot_id=bot.id, tenant=tenant, env=env
        )
        # fmt: off
        device = next(
            (
                d
                for d in devices
                if d.device_uuid == device_uuid
            ),
            None,
        )
        # fmt: on
        if not device:
            logger.warning(f"Device {device_uuid} not found for bot {bot_uuid}")
            raise NoDevicesFoundError(
                f"Device {device_uuid} not found for bot {bot_uuid}"
            )

        # 3. Validate device is ACTIVE
        if device.status != "ACTIVE":
            logger.warning(
                f"Device {device_uuid} is not active (status={device.status})"
            )
            raise NoActiveDevicesError(
                f"Device {device_uuid} is not active (status={device.status})"
            )

        # 4. Validate provider_device_id
        paas_device_id = device.provider_device_id
        if paas_device_id is None:
            raise RuntimeError(f"Device {device_uuid} has no provider_device_id")

        logger.info(
            f"Using specific device: device_uuid={device_uuid}, "
            f"paas_device_id={paas_device_id}"
        )

        # 5. Resolve HTTP connection info via PaaS facade
        conn_info = await self._paas_facade.resolve_invoke_http_info(
            paas_device_id=paas_device_id,
            port=port,
            path=path,
        )

        logger.info(
            f"Dispatched HTTP connection for specific device: "
            f"device_uuid={device_uuid}, http_url={conn_info.http_url}"
        )

        return conn_info
