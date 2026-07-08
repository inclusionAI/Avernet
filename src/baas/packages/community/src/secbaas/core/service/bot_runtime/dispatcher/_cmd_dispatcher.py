"""DefaultBotCmdDispatcher — shell command execution.

Extends BaseDispatcher to dispatch shell commands to a bot's active device.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from secbaas.api.bot_runtime import BotCmdDispatcher
from secbaas.api.device_manage import CommandResult
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


class DefaultBotCmdDispatcher(BotBaseDispatcher, BotCmdDispatcher):
    """Shell command execution on a bot's active device.

    Implements CmdDispatcher protocol for command execution.
    Inherits __init__ and _resolve_bot_device from BaseDispatcher.
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        device_repo: DeviceRepository,
        paas_facade: PaasServiceFacade,
    ):
        super().__init__(bot_repo, device_repo, paas_facade)

    async def dispatch_bot_execute_command(
        self,
        bot_uuid: str,
        cmd: str,
        tenant: str,
        cmd_env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        device_affinity: str | None = None,
    ) -> CommandResult:
        """Execute a shell command on a bot's active device."""
        env = get_current_env()
        logger.info(
            f"Dispatching bot cmd execute: bot_uuid={bot_uuid}, "
            f"cmd={cmd[:100]!r}, tenant={tenant}"
        )

        _, _, paas_device_id = await self._resolve_bot_device(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=env,
            device_affinity=device_affinity,
        )

        logger.info(
            f"Using paas_device_id: {paas_device_id}, timeout={timeout_seconds}s"
        )

        result = await self._paas_facade.execute_command(
            paas_device_id=paas_device_id,
            cmd=cmd,
            env=cmd_env,
            timeout_seconds=timeout_seconds,
        )

        logger.info(
            f"Command executed: bot_uuid={bot_uuid}, "
            f"exit_code={result.exit_code}, "
            f"execution_time_ms={result.execution_time_ms}"
        )

        return result
