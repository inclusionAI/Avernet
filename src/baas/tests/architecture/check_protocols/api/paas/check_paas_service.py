from unittest.mock import MagicMock

from secbaas.community.api.device_manage import ArcaCredentials
from secbaas.community.api.paas import PaasService as PaasServiceProtocol
from secbaas.community.core.service.paas import ArcaPaasService
from secbaas.community.spi.sandbox.arca import ArcaSandboxPlugin

# Assign value, will trigger mypy type check
_paas_service: PaasServiceProtocol = ArcaPaasService(
    credentials=MagicMock(spec=ArcaCredentials),
    arca_sandbox_plugin=MagicMock(spec=ArcaSandboxPlugin),
)
