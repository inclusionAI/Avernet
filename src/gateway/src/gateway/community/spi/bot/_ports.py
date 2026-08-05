"""Bot-domain SPI — the ``BotRegistry`` contract.

A bot is resolved from a presented token by a :class:`BotRegistry`
implementation (the canonical ORM impl lives in ``core/bot``). The authn
``bot_token`` strategy depends on this interface, not on the impl.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RegisteredBot:
    """A bot a registry resolves a session token to (registry record).

    ``owner_id`` is the bot's creator/owner (resource-ownership anchor, from the
    DB ``created_by`` column); ``app_id`` is the app the bot belongs to (the
    surrogate ``id`` of ``avernet_application``); ``agent_code`` is the bot's
    agent/engine code; ``tenant`` is its tenant. ``env`` stays DB-side only.
    """

    bot_uuid: str
    owner_id: str
    app_id: int
    agent_code: str
    tenant: str


class BotRegistry(Protocol):
    """Read-only bot store keyed by token (resolved by the ``bot_token`` strategy).

    ``find_bot_by_token`` returns ``None`` for an unknown token (soft miss —
    not applicable), never raising on a bad token.
    """

    async def find_bot_by_token(self, token: str) -> RegisteredBot | None: ...

    async def find_bot_by_agent_code(
        self, agent_code: str
    ) -> RegisteredBot | None:
        """Resolve a bot by its ``agent_code`` (Provider-facing agent/engine code).

        Returns ``None`` for an unknown ``agent_code`` (soft miss — not
        applicable); never raises on an unknown code. Used by the agentpass
        agent_code fallback path to map an Agent Server SDK-resolved
        ``agent_code`` to a registered bot.
        """
        ...
