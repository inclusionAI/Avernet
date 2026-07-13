from unittest.mock import MagicMock

from secbaas.community.api.api_gateway import APIKeyService as APIKeyServiceProtocol
from secbaas.community.core.repository.api_gateway import APIKeyRepository
from secbaas.community.core.service.api_gateway import DefaultAPIKeyService

# Assign value, will trigger mypy type check
_api_key_service: APIKeyServiceProtocol = DefaultAPIKeyService(
    repository=MagicMock(spec=APIKeyRepository),
)
