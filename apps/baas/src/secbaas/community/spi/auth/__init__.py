"""Auth plugin SPI — unified authentication + authorization protocol.

This replaces the separate spi/identity and spi/permission protocols.
A single AuthPlugin covers login, whitelist, and permission checking.
"""

from ._errors import AuthError
from ._models import AuthUser
from ._protocols import AuthPlugin

__all__ = [
    "AuthError",
    "AuthPlugin",
    "AuthUser",
]
