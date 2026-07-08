from unittest.mock import MagicMock

from secbaas.api.bot_runtime import BotWssDispatcher as BotWssDispatcherProtocol
from secbaas.api.device_manage import PaasServiceFacade
from secbaas.core.repository.bot import BotRepository
from secbaas.core.repository.device import DeviceRepository
from secbaas.core.service.bot_runtime.dispatcher import DefaultBotWssDispatcher

# Assign value, will trigger mypy type check
_bot_wss_dispatcher: BotWssDispatcherProtocol = DefaultBotWssDispatcher(
    bot_repo=MagicMock(spec=BotRepository),
    device_repo=MagicMock(spec=DeviceRepository),
    paas_facade=MagicMock(spec=PaasServiceFacade),
)
