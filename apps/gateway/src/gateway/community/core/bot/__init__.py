"""Bot domain — canonical data-access (``BotRegistry`` SPI impl).

Holds the ORM row (:class:`BotRow`) and the canonical
:class:`BotRepository` impl. The :class:`~gateway.community.spi.bot.BotRegistry`
contract lives in the bot SPI. The authn ``bot_token`` strategy depends on the
SPI, not this module.
"""

from ._orm import BotRow
from ._repository import BotRepository

__all__ = [
    "BotRepository",
    "BotRow",
]
