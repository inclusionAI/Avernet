from unittest.mock import MagicMock

from secbaas.community.api.bot_runtime import (
    BotCmdDispatcher as BotCmdDispatcherProtocol,
)
from secbaas.community.api.device_manage import PaasServiceFacade
from secbaas.community.core.repository.bot import BotRepository
from secbaas.community.core.repository.device import DeviceRepository
from secbaas.community.core.service.bot_runtime.dispatcher import (
    DefaultBotCmdDispatcher,
)

# Assign value, will trigger mypy type check
_bot_cmd_dispatcher: BotCmdDispatcherProtocol = DefaultBotCmdDispatcher(
    bot_repo=MagicMock(spec=BotRepository),
    device_repo=MagicMock(spec=DeviceRepository),
    paas_facade=MagicMock(spec=PaasServiceFacade),
)
