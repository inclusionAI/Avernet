"""Bot-domain SPI — the ``BotRegistry`` contract.

See ``_ports`` for the :class:`BotRegistry` protocol and the
:class:`RegisteredBot` record. The canonical ORM implementation lives in
``core/bot``; the authn ``bot_token`` strategy depends on this interface.
"""

from ._ports import BotRegistry, RegisteredBot

__all__ = [
    "BotRegistry",
    "RegisteredBot",
]
