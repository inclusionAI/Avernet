"""``bot_token`` strategy — bot session token → ``BotRegistry`` lookup.

The :class:`BotRegistry`/`RegisteredBot` contracts live in the authn SPI
(``gateway.community.spi.authn``); the DB-backed registry implementation is a
separate data-access capability (``plugins/authn/bot_registry/``). This package
holds only the strategy.
"""

from ._strategy import BotTokenStrategy, extract_bot_token, is_jwt_format

__all__ = [
    "BotTokenStrategy",
    "extract_bot_token",
    "is_jwt_format",
]
