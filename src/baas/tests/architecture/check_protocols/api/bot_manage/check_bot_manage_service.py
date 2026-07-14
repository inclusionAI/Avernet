from unittest.mock import MagicMock

from secbaas.community.api.bot_manage import (
    BotCrudService,
)
from secbaas.community.api.bot_manage import (
    BotManageService as BotManageServiceProtocol,
)
from secbaas.community.api.health_check.bot import BotHealthCheckerService
from secbaas.community.api.publish_manage import PublishService
from secbaas.community.core.repository.bot import BotRepository
from secbaas.community.core.repository.device import DeviceRepository
from secbaas.community.core.repository.system_config import SystemConfigRepository
from secbaas.community.core.service.bot_manage import DefaultBotManagementService

# Assign value, will trigger mypy type check
_bot_manage_service: BotManageServiceProtocol = DefaultBotManagementService(
    bot_repo=MagicMock(spec=BotRepository),
    device_repo=MagicMock(spec=DeviceRepository),
    system_config_repo=MagicMock(spec=SystemConfigRepository),
    publish_service=MagicMock(spec=PublishService),
    bot_service=MagicMock(spec=BotCrudService),
    health_checker=MagicMock(spec=BotHealthCheckerService),
)
