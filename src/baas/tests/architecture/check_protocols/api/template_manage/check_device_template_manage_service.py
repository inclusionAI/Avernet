from unittest.mock import MagicMock

from secbaas.community.api.template_manage import (
    DeviceTemplateManageService as DeviceTemplateManageServiceProtocol,
)
from secbaas.community.api.tenant_manage import TenantManageService
from secbaas.community.core.repository.device_template import DeviceTemplateRepository
from secbaas.community.core.service.template_manage import DefaultDeviceTemplateService
from secbaas.community.spi.secret import SecretStorePlugin

# Assign value, will trigger mypy type check
_device_template_manage_service: DeviceTemplateManageServiceProtocol = (
    DefaultDeviceTemplateService(
        repository=MagicMock(spec=DeviceTemplateRepository),
        tenant_service=MagicMock(spec=TenantManageService),
        secret_plugin=MagicMock(spec=SecretStorePlugin),
    )
)
