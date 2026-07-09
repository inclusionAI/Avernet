from unittest.mock import MagicMock

from secbaas.api.device_manage import (
    DeviceService as DeviceServiceProtocol,
)
from secbaas.api.device_manage import (
    PaasServiceFacade,
)
from secbaas.api.template_manage import DeviceTemplateManageService
from secbaas.core.repository.device import DeviceRepository
from secbaas.core.service.device_manage import DefaultDeviceService
from secbaas.spi.secret import SecretStorePlugin

# Assign value, will trigger mypy type check
_device_service: DeviceServiceProtocol = DefaultDeviceService(
    paas_facade=MagicMock(spec=PaasServiceFacade),
    repository=MagicMock(spec=DeviceRepository),
    device_template_service=MagicMock(spec=DeviceTemplateManageService),
    secret_plugin=MagicMock(spec=SecretStorePlugin),
)
