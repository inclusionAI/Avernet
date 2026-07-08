from unittest.mock import MagicMock

from secbaas.api.bot_runtime import BotRunner as BotRunnerProtocol
from secbaas.core.repository.bot_run import BotRunRepository
from secbaas.core.service.bot_run import (
    BotRunner,
    BotServiceSelector,
)
from secbaas.core.service.bot_run._internal_protocols import MessageDispatcher
from secbaas.spi.bot_service import BotServicePlugin

# Assign value, will trigger mypy type check
_bot_runner: BotRunnerProtocol = BotRunner(
    bot_service_selector=MagicMock(spec=BotServiceSelector),
    run_repository=MagicMock(spec=BotRunRepository),
    bot_service_plugin=MagicMock(spec=BotServicePlugin),
    dispatcher=MagicMock(spec=MessageDispatcher),
)
