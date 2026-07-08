from unittest.mock import MagicMock

from secbaas.api.bot_manage import BotCrudService as BotCrudServiceProtocol
from secbaas.api.device_manage import DeviceService
from secbaas.api.template_manage import DeviceTemplateManageService
from secbaas.core.repository.bot import BotRepository
from secbaas.core.repository.bot_device_rel import BotDeviceRelRepository
from secbaas.core.repository.device import DeviceRepository
from secbaas.core.service.bot_manage import DefaultBotCrudService

# Assign value, will trigger mypy type check
_bot_crud_service: BotCrudServiceProtocol = DefaultBotCrudService(
    bot_repo=MagicMock(spec=BotRepository),
    device_repo=MagicMock(spec=DeviceRepository),
    rel_repo=MagicMock(spec=BotDeviceRelRepository),
    device_template_service=MagicMock(spec=DeviceTemplateManageService),
    device_service=MagicMock(spec=DeviceService),
)
