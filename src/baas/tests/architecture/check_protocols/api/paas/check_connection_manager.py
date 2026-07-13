from unittest.mock import MagicMock

from secbaas.community.api.paas import ConnectionManager as ConnectionManagerProtocol
from secbaas.community.core.repository.local_user_machine import (
    LocalUserMachineRepository,
)
from secbaas.community.core.service.paas.desktop import ConnectionManager

# Assign value, will trigger mypy type check
_connection_manager: ConnectionManagerProtocol = ConnectionManager(
    repository=MagicMock(spec=LocalUserMachineRepository),
)
