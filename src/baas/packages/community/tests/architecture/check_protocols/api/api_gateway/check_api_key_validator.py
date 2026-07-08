from unittest.mock import MagicMock

from secbaas.api.api_gateway import APIKeyValidator as APIKeyValidatorProtocol
from secbaas.core.repository.api_gateway import APIKeyRepository
from secbaas.core.service.api_gateway import DefaultAPIKeyValidator

# Assign value, will trigger mypy type check
_api_key_validator: APIKeyValidatorProtocol = DefaultAPIKeyValidator(
    repository=MagicMock(spec=APIKeyRepository),
)
