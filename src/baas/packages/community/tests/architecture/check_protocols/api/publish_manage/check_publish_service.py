from unittest.mock import MagicMock

from secbaas.api.bot_manage import BotManageService
from secbaas.api.device_manage import DeviceService
from secbaas.api.publish_manage import PublishService as PublishServiceProtocol
from secbaas.api.template_manage import DeviceTemplateManageService
from secbaas.core.repository.bot import BotRepository
from secbaas.core.repository.bot_device_rel import BotDeviceRelRepository
from secbaas.core.repository.bot_session import BotSessionRepository
from secbaas.core.repository.device import DeviceRepository
from secbaas.core.repository.publish import PublishRepository
from secbaas.core.repository.publish_batch import PublishBatchRepository
from secbaas.core.repository.publish_record import PublishRecordRepository
from secbaas.core.service.publish_manage import DefaultPublishService

# Assign value, will trigger mypy type check
_publish_service: PublishServiceProtocol = DefaultPublishService(
    bot_repo=MagicMock(spec=BotRepository),
    device_repo=MagicMock(spec=DeviceRepository),
    rel_repo=MagicMock(spec=BotDeviceRelRepository),
    session_repo=MagicMock(spec=BotSessionRepository),
    publish_repo=MagicMock(spec=PublishRepository),
    batch_repo=MagicMock(spec=PublishBatchRepository),
    publish_record_repo=MagicMock(spec=PublishRecordRepository),
    template_service=MagicMock(spec=DeviceTemplateManageService),
    bot_service=MagicMock(spec=BotManageService),
    device_service=MagicMock(spec=DeviceService),
)
