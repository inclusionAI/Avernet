from unittest.mock import MagicMock

from secbaas.community.api.bot_runtime import BotRunner as BotRunnerProtocol
from secbaas.community.core.repository.bot_run import BotRunRepository
from secbaas.community.core.service.bot_run import (
    BotRunner,
    BotServiceSelector,
)
from secbaas.community.core.service.bot_run._internal_protocols import MessageDispatcher
from secbaas.community.spi.bot_service import BotServicePlugin

# Assign value, will trigger mypy type check
_bot_runner: BotRunnerProtocol = BotRunner(
    bot_service_selector=MagicMock(spec=BotServiceSelector),
    run_repository=MagicMock(spec=BotRunRepository),
    bot_service_plugin=MagicMock(spec=BotServicePlugin),
    dispatchers=[MagicMock(spec=MessageDispatcher)],
)
