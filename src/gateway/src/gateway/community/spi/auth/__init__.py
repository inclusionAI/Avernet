"""Auth plugin SPI — unified authentication + authorization protocol."""

from ._errors import AuthError
from ._models import AuthenticatedUser
from ._protocols import AuthPlugin

__all__ = [
    "AuthError",
    "AuthPlugin",
    "AuthenticatedUser",
]
