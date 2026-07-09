from unittest.mock import MagicMock

from secbaas.api.auth import AuthService as AuthServiceProtocol
from secbaas.core.service.auth_service import AuthService
from secbaas.spi.auth import AuthPlugin

# Assign value, will trigger mypy type check
_auth_service: AuthServiceProtocol = AuthService(
    plugin=MagicMock(spec=AuthPlugin),
)
