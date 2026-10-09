"""DefaultBotOpenFolderDispatcher — open folder on a bot's active device.

Extends BaseDispatcher to dispatch an open-folder command to a bot's active device.
Only supported on LOCAL platform.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from secbaas.community.api.bot_runtime import BotOpenFolderDispatcher
from secbaas.community.core.service.paas import PaasServiceFacade
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

if TYPE_CHECKING:
    from secbaas.community.core.repository.bot import BotRepository
    from secbaas.community.core.repository.device import DeviceRepository

from ._base_dispatcher import (
    BotBaseDispatcher,
)

logger = get_logger("core-service")


class DefaultBotOpenFolderDispatcher(BotBaseDispatcher, BotOpenFolderDispatcher):
    """Open a folder on a bot's active device.

    Resolves bot_uuid to paas_device_id and delegates open_folder
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

    async def dispatch_bot_open_folder(
        self,
        bot_uuid: str,
        tenant: str,
        folder_path: str | None = None,
        device_affinity: str | None = None,
    ) -> bool:
        """Open a folder in a bot's active device file explorer.

        Only supported on LOCAL platform. Non-LOCAL platforms raise
        DeviceFacadeException with PLATFORM_ERROR.
        """
        env = get_current_env()
        logger.info(
            f"Dispatching bot open_folder: bot_uuid={bot_uuid}, "
            f"folder_path={folder_path!r}, tenant={tenant}"
        )

        _, _, paas_device_id = await self._resolve_bot_device(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=env,
            device_affinity=device_affinity,
        )

        logger.info(f"Using paas_device_id for open_folder: {paas_device_id}")

        result = await self._paas_facade.open_folder(
            paas_device_id=paas_device_id,
            folder_path=folder_path,
        )

        logger.info(
            f"Open folder completed: bot_uuid={bot_uuid}, "
            f"paas_device_id={paas_device_id}"
        )

        return result
