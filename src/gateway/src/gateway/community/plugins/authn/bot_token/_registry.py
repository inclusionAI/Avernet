"""In-memory bot registry (community edition).

A bot session token resolves the bot in **one** lookup:
``find_bot_by_token(token) -> Bot | None``. The registry is read-only over its
token → :class:`Bot` map; the ``bare`` flavor seeds it with a demo bot so the
open-source edition can exercise bot identity out of the box. The
``BotRegistry`` protocol exposes only ``find_bot_by_token`` — how bots get into
the registry (seeding, a DB-backed loader, etc.) is an implementation detail of
each flavor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Bot:
    """A bot the registry can resolve a token to.

    ``owner_id`` is the bot's creator/owner; ``tenant`` is the owner's tenant.
    Both are required — the gateway's ``BotPrincipal`` needs an owner/tenant
    anchor.
    """

    bot_uuid: str
    owner_id: str
    tenant: str


class BotRegistry(Protocol):
    """Read-only bot store keyed by token (community flavor)."""

    async def find_bot_by_token(self, token: str) -> Bot | None:
        """Resolve ``token`` to its :class:`Bot`, or ``None`` if unknown."""
        ...


class InMemoryBotRegistry:
    """In-memory token → :class:`Bot` map (bare/community edition).

    Seeded with one demo bot; ``find_bot_by_token`` is the only operation.
    Subclass or pass seeds via ``entries`` to populate it differently.
    """

    def __init__(self, *, entries: dict[str, Bot] | None = None) -> None:
        if entries is not None:
            self._by_token: dict[str, Bot] = dict(entries)
            return
        self._by_token = {
            "bot-key": Bot(bot_uuid="bot-7", owner_id="owner-1", tenant="t")
        }

    async def find_bot_by_token(self, token: str) -> Bot | None:
        return self._by_token.get(token)
