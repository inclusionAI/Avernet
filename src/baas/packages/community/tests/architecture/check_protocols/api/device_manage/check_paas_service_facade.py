from unittest.mock import MagicMock

from secbaas.api.device_manage import PaasServiceFacade as PaasServiceFacadeProtocol
from secbaas.api.template_manage import DeviceTemplateManageService
from secbaas.core.repository.device import DeviceRepository
from secbaas.core.service.paas import PaasServiceFacade, PaasServiceFactory

# Assign value, will trigger mypy type check
_paas_service_facade: PaasServiceFacadeProtocol = PaasServiceFacade(
    device_repository=MagicMock(spec=DeviceRepository),
    device_template_service=MagicMock(spec=DeviceTemplateManageService),
    factory=MagicMock(spec=PaasServiceFactory),
)
