from unittest.mock import MagicMock

from secbaas.api.paas import PaasServiceFactory as PaasServiceFactoryProtocol
from secbaas.api.template_manage import DeviceTemplateManageService
from secbaas.core.repository.device import DeviceRepository
from secbaas.core.repository.device_template import DeviceTemplateRepository
from secbaas.core.repository.local_user_machine import LocalUserMachineRepository
from secbaas.core.repository.publish_record import PublishRecordRepository
from secbaas.core.service.paas import PaasServiceFactory
from secbaas.core.service.paas.desktop import ConnectionManager
from secbaas.spi.sandbox import PaasSandboxPlugins
from secbaas.spi.secret import SecretStorePlugin

# Assign value, will trigger mypy type check
_paas_service_factory: PaasServiceFactoryProtocol = PaasServiceFactory(
    template_service=MagicMock(spec=DeviceTemplateManageService),
    connection_manager=MagicMock(spec=ConnectionManager),
    worker_router=MagicMock(),
    instance_router=MagicMock(),
    device_template_repository=MagicMock(spec=DeviceTemplateRepository),
    device_repository=MagicMock(spec=DeviceRepository),
    publish_record_repository=MagicMock(spec=PublishRecordRepository),
    local_user_machine_repository=MagicMock(spec=LocalUserMachineRepository),
    paas_sandbox_plugins=MagicMock(spec=PaasSandboxPlugins),
    secret_plugin=MagicMock(spec=SecretStorePlugin),
)
