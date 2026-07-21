"""Auth plugin SPI — unified authentication + authorization protocol."""

from ._errors import AuthError
from ._models import AuthUser
from ._protocols import AuthPlugin

__all__ = [
    "AuthError",
    "AuthPlugin",
    "AuthUser",
]
