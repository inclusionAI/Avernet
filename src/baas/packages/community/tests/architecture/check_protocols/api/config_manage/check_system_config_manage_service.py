from unittest.mock import MagicMock

from secbaas.api.config_manage import (
    SystemConfigManageService as SystemConfigManageServiceProtocol,
)
from secbaas.core.repository.system_config import SystemConfigRepository
from secbaas.core.service.config_manage import DefaultSystemConfigManageService

# Assign value, will trigger mypy type check
_system_config_manage_service: SystemConfigManageServiceProtocol = (
    DefaultSystemConfigManageService(
        repository=MagicMock(spec=SystemConfigRepository),
    )
)
