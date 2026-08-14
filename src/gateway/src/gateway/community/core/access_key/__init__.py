"""Access-key domain — canonical data-access (``AccessKeyRegistry`` SPI impl).

Holds the ORM row (:class:`AccessKeyRow`), the canonical
:class:`AccessKeyRepository` impl, and :class:`AccessKeyIssuer` (mints + persists
access keys). The :class:`~gateway.community.spi.access_key.AccessKeyRegistry`
contract lives in the access-key SPI. The authn ``access_key_token`` strategy
depends on the SPI, not this module.
"""

from ._issuer import AccessKeyIssuer, IssuedAccessKey
from ._orm import AccessKeyRow
from ._repository import AccessKeyRepository

__all__ = [
    "AccessKeyIssuer",
    "AccessKeyRepository",
    "AccessKeyRow",
    "IssuedAccessKey",
]
