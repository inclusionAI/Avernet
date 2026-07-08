from unittest.mock import MagicMock

from secbaas.api.publish_manage import (
    PublishAdminService as PublishAdminServiceProtocol,
)
from secbaas.core.repository.bot import BotRepository
from secbaas.core.repository.device import DeviceRepository
from secbaas.core.repository.publish import PublishRepository
from secbaas.core.repository.publish_batch import PublishBatchRepository
from secbaas.core.repository.publish_record import PublishRecordRepository
from secbaas.core.service.publish_manage import DefaultPublishAdminService

# Assign value, will trigger mypy type check
_publish_admin_service: PublishAdminServiceProtocol = DefaultPublishAdminService(
    publish_repo=MagicMock(spec=PublishRepository),
    batch_repo=MagicMock(spec=PublishBatchRepository),
    record_repo=MagicMock(spec=PublishRecordRepository),
    device_repo=MagicMock(spec=DeviceRepository),
    bot_repo=MagicMock(spec=BotRepository),
)
