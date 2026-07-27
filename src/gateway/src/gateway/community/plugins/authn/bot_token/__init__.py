"""``bot_token`` auth strategy plugin — bot credential → BotPrincipal.

Mirrors BCS ``bcs-auth-session::SessionTokenPlugin`` + ``BotRegistryLookup`` in
one place: :class:`BotTokenStrategy` extracts a bot session token and resolves
it by composing the in-memory :class:`InMemoryBotRegistry` (like BCS
``BotRegistryCoreService``) directly — no separate validator abstraction.
"""

from ._registry import Bot, BotRegistry, InMemoryBotRegistry
from ._strategy import BotTokenStrategy

__all__ = [
    "Bot",
    "BotRegistry",
    "BotTokenStrategy",
    "InMemoryBotRegistry",
]
