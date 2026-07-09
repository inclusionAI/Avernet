from unittest.mock import MagicMock

from secbaas.api.bot_runtime import BotHttpDispatcher as BotHttpDispatcherProtocol
from secbaas.api.device_manage import PaasServiceFacade
from secbaas.core.repository.bot import BotRepository
from secbaas.core.repository.device import DeviceRepository
from secbaas.core.service.bot_runtime.dispatcher import DefaultBotHttpDispatcher

# Assign value, will trigger mypy type check
_bot_http_dispatcher: BotHttpDispatcherProtocol = DefaultBotHttpDispatcher(
    bot_repo=MagicMock(spec=BotRepository),
    device_repo=MagicMock(spec=DeviceRepository),
    paas_facade=MagicMock(spec=PaasServiceFacade),
)
