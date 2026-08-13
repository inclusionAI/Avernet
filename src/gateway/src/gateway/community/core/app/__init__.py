"""App domain — canonical data-access (``AppRegistry`` SPI impl) + registration.

Holds the ORM row (:class:`AppRow`), the canonical :class:`AppRepository` impl,
:class:`AppRegistrar` (registers + persists apps), and :class:`APIKeyGenerator`
(the app credential scheme). The
:class:`~gateway.community.spi.app.AppRegistry` contract lives in the app SPI.
The authn ``app_token`` strategy depends on the SPI, not this module.
"""

from ._key_gen import APIKeyGenerator
from ._orm import AppRow
from ._registrar import AppRegistrar, IssuedApp
from ._repository import AppRepository

__all__ = [
    "APIKeyGenerator",
    "AppRegistrar",
    "AppRepository",
    "AppRow",
    "IssuedApp",
]
