from unittest.mock import MagicMock

from secbaas.community.api.device_manage import (
    LocalCredentials,
)
from secbaas.community.api.device_manage import (
    LocalPaasService as LocalPaasServiceProtocol,
)
from secbaas.community.core.repository.local_user_machine import (
    LocalUserMachineRepository,
)
from secbaas.community.core.service.paas import LocalPaasService
from secbaas.community.core.service.paas.desktop import ConnectionManager
from secbaas.community.core.service.paas.desktop.instance_router import InstanceRouter
from secbaas.community.spi.sandbox.desktop import DesktopSandboxPlugin

# Assign value, will trigger mypy type check
_local_paas_service: LocalPaasServiceProtocol = LocalPaasService(
    credentials=MagicMock(spec=LocalCredentials),
    repository=MagicMock(spec=LocalUserMachineRepository),
    connection_manager=MagicMock(spec=ConnectionManager),
    instance_router=MagicMock(spec=InstanceRouter),
    server_ip="127.0.0.1",
    desktop_sandbox_plugin=MagicMock(spec=DesktopSandboxPlugin),
)
