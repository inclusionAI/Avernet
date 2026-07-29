"""Access-key domain — canonical data-access (``AccessKeyRegistry`` SPI impl).

Holds the ORM row (:class:`AccessKeyRow`) and the canonical
:class:`AccessKeyRepository` impl. The
:class:`~gateway.community.spi.access_key.AccessKeyRegistry` contract lives in
the access-key SPI. The authn ``access_key_token`` strategy depends on the SPI,
not this module.
"""

from ._orm import AccessKeyRow
from ._repository import AccessKeyRepository

__all__ = [
    "AccessKeyRepository",
    "AccessKeyRow",
]
