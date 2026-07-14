from unittest.mock import MagicMock

from secbaas.community.api.auth import AuthService as AuthServiceProtocol
from secbaas.community.core.service.auth_service import AuthService
from secbaas.community.spi.auth import AuthPlugin

# Assign value, will trigger mypy type check
_auth_service: AuthServiceProtocol = AuthService(
    plugin=MagicMock(spec=AuthPlugin),
)
