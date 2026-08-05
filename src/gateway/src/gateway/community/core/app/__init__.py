"""App domain — canonical data-access (``AppRegistry`` SPI impl) + registration.

Holds the ORM row (:class:`AppRow`), the canonical :class:`AppRepository` impl,
and :class:`AppRegistrar` (registers + persists apps). The
:class:`~gateway.community.spi.app.AppRegistry` contract lives in the app SPI.
The authn ``app_token`` strategy depends on the SPI, not this module.
"""

from ._orm import AppRow
from ._registrar import AppRegistrar, IssuedApp
from ._repository import AppRepository

__all__ = [
    "AppRegistrar",
    "AppRepository",
    "AppRow",
    "IssuedApp",
]
