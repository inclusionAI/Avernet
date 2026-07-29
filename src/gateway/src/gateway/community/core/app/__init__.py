"""App domain — canonical data-access (``AppRegistry`` SPI impl).

Holds the ORM row (:class:`AppRow`) and the canonical :class:`AppRepository`
impl. The :class:`~gateway.community.spi.app.AppRegistry` contract lives in the
app SPI. The authn ``app_token`` strategy depends on the SPI, not this module.
"""

from ._orm import AppRow
from ._repository import AppRepository

__all__ = [
    "AppRepository",
    "AppRow",
]
