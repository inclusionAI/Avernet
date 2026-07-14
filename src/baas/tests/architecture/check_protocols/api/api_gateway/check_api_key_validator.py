from unittest.mock import MagicMock

from secbaas.community.api.api_gateway import APIKeyValidator as APIKeyValidatorProtocol
from secbaas.community.core.repository.api_gateway import APIKeyRepository
from secbaas.community.core.service.api_gateway import DefaultAPIKeyValidator

# Assign value, will trigger mypy type check
_api_key_validator: APIKeyValidatorProtocol = DefaultAPIKeyValidator(
    repository=MagicMock(spec=APIKeyRepository),
)
