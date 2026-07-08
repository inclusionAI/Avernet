from unittest.mock import MagicMock

from secbaas.api.api_gateway import APIKeyService as APIKeyServiceProtocol
from secbaas.core.repository.api_gateway import APIKeyRepository
from secbaas.core.service.api_gateway import DefaultAPIKeyService

# Assign value, will trigger mypy type check
_api_key_service: APIKeyServiceProtocol = DefaultAPIKeyService(
    repository=MagicMock(spec=APIKeyRepository),
)
