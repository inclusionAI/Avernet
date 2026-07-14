from unittest.mock import MagicMock

from secbaas.community.api.publish_manage import (
    PublishAdminService as PublishAdminServiceProtocol,
)
from secbaas.community.core.repository.bot import BotRepository
from secbaas.community.core.repository.device import DeviceRepository
from secbaas.community.core.repository.publish import PublishRepository
from secbaas.community.core.repository.publish_batch import PublishBatchRepository
from secbaas.community.core.repository.publish_record import PublishRecordRepository
from secbaas.community.core.service.publish_manage import DefaultPublishAdminService

# Assign value, will trigger mypy type check
_publish_admin_service: PublishAdminServiceProtocol = DefaultPublishAdminService(
    publish_repo=MagicMock(spec=PublishRepository),
    batch_repo=MagicMock(spec=PublishBatchRepository),
    record_repo=MagicMock(spec=PublishRecordRepository),
    device_repo=MagicMock(spec=DeviceRepository),
    bot_repo=MagicMock(spec=BotRepository),
)
