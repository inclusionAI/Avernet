from unittest.mock import MagicMock

from secbaas.api.bot_manage import (
    BotCrudService,
)
from secbaas.api.bot_manage import (
    BotManageService as BotManageServiceProtocol,
)
from secbaas.api.health_check.bot import BotHealthCheckerService
from secbaas.api.publish_manage import PublishService
from secbaas.core.repository.bot import BotRepository
from secbaas.core.repository.device import DeviceRepository
from secbaas.core.repository.system_config import SystemConfigRepository
from secbaas.core.service.bot_manage import DefaultBotManagementService

# Assign value, will trigger mypy type check
_bot_manage_service: BotManageServiceProtocol = DefaultBotManagementService(
    bot_repo=MagicMock(spec=BotRepository),
    device_repo=MagicMock(spec=DeviceRepository),
    system_config_repo=MagicMock(spec=SystemConfigRepository),
    publish_service=MagicMock(spec=PublishService),
    bot_service=MagicMock(spec=BotCrudService),
    health_checker=MagicMock(spec=BotHealthCheckerService),
)
