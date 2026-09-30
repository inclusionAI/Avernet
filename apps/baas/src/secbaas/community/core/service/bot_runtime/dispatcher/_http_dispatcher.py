"""DefaultBotHttpDispatcher — HTTP invocation.

Extends BaseDispatcher to dispatch HTTP invocations to a bot's active device.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import BotHttpDispatcher
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


class DefaultBotHttpDispatcher(BotBaseDispatcher, BotHttpDispatcher):
    """HTTP invocation to a bot's active device.

    Implements HttpDispatcher protocol for HTTP invocation.
    Inherits __init__ and _resolve_bot_device from BaseDispatcher.
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        device_repo: DeviceRepository,
        paas_facade: PaasServiceFacade,
    ):
        super().__init__(bot_repo, device_repo, paas_facade)

    async def dispatch_bot_http_invoke(
        self,
        bot_uuid: str,
        method: str,
        port: int,
        path: str,
        query_string: str | None,
        headers: dict[str, str],
        body: bytes,
        tenant: str,
        device_affinity: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch HTTP invocation to a bot's active device."""
        env = get_current_env()
        logger.info(
            f"Dispatching HTTP invoke: bot_uuid={bot_uuid}, method={method}, "
            f"port={port}, path={path}, tenant={tenant}"
        )

        _, _, paas_device_id = await self._resolve_bot_device(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=env,
            device_affinity=device_affinity,
        )

        logger.info(f"Using paas_device_id: {paas_device_id}")

        result = await self._paas_facade.invoke_http_in_device(
            paas_device_id=paas_device_id,
            method=method,
            port=port,
            path=path,
            query_string=query_string,
            headers=headers,
            body=body,
        )

        logger.info(f"HTTP invoke dispatched: status_code={result.get('status_code')}")

        return result
