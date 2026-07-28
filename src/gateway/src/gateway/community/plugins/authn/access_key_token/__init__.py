"""``access_key_token`` strategy — access-key token → ``AccessKeyRegistry`` lookup.

The :class:`AccessKeyRegistry`/`RegisteredAccessKey` contracts live in the
authn SPI (``gateway.community.spi.authn``); the DB-backed registry
implementation is a separate data-access capability
(``plugins/authn/access_key_registry/``). This package holds only the strategy.
"""

from ._strategy import AccessKeyTokenStrategy

__all__ = [
    "AccessKeyTokenStrategy",
]
