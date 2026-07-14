from unittest.mock import MagicMock

from secbaas.community.api.bot_manage import BotCrudService as BotCrudServiceProtocol
from secbaas.community.api.device_manage import DeviceService
from secbaas.community.api.template_manage import DeviceTemplateManageService
from secbaas.community.core.repository.bot import BotRepository
from secbaas.community.core.repository.bot_device_rel import BotDeviceRelRepository
from secbaas.community.core.repository.device import DeviceRepository
from secbaas.community.core.service.bot_manage import DefaultBotCrudService

# Assign value, will trigger mypy type check
_bot_crud_service: BotCrudServiceProtocol = DefaultBotCrudService(
    bot_repo=MagicMock(spec=BotRepository),
    device_repo=MagicMock(spec=DeviceRepository),
    rel_repo=MagicMock(spec=BotDeviceRelRepository),
    device_template_service=MagicMock(spec=DeviceTemplateManageService),
    device_service=MagicMock(spec=DeviceService),
)
