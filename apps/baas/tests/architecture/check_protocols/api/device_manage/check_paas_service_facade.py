from unittest.mock import MagicMock

from secbaas.community.api.device_manage import (
    PaasServiceFacade as PaasServiceFacadeProtocol,
)
from secbaas.community.api.template_manage import DeviceTemplateManageService
from secbaas.community.core.repository.device import DeviceRepository
from secbaas.community.core.service.paas import PaasServiceFacade, PaasServiceFactory

# Assign value, will trigger mypy type check
_paas_service_facade: PaasServiceFacadeProtocol = PaasServiceFacade(
    device_repository=MagicMock(spec=DeviceRepository),
    device_template_service=MagicMock(spec=DeviceTemplateManageService),
    factory=MagicMock(spec=PaasServiceFactory),
)
