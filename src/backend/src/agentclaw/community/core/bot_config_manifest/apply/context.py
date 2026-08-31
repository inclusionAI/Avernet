"""Who and what one apply runs as.

Built once at the top of an apply and handed to every materialiser, so that a
materialiser never re-derives an identity or a coordinate. The coordinates come
from **W10's seam** (``core/bot_config_surface``) — the same functions the
public API's routers call — which is what makes "apply enforces what the API
enforces" a property of the code rather than a claim in a review.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.bot_config_surface.coords import BotConfigCoords


@dataclass(frozen=True)
class ApplyContext:
    """The identity and addressing one apply runs under.

    ``owner_id`` and ``actor_id`` differ on a shared bot: the bot is resolved as
    the *owner's*, while the actor is whoever is applying. Materialisers that
    call a bot-configuration service pass both, exactly as the routers do.
    """

    bot_id: str
    #: The bot's owner. What the addressed-bot coordinates resolve against.
    owner_id: str
    #: Who is applying. On a shared bot this is a collaborator, not the owner —
    #: the distinction an audit field must not lose.
    actor_id: str
    #: Storage key, resolved server-side from the bot record. Never a request
    #: parameter and never a response field.
    entity_id: str
    env: str
    tenant: str
    engine_type: str
    bot_type: str
    #: The bot record, for the seam constructors that read one.
    bot: dict[str, Any]

    def coords_for(self, category: str, **extra: Any) -> BotConfigCoords:
        """This bot's write address for one category, via W10's seam.

        Never computed here. ``CONFIG_SURFACE`` holds the same function object
        the category's router calls, so a rule that moves stays in one place —
        which is the whole reason that seam exists (#1509).

        **The import is lazy, and that is not style.** ``bot_config_surface.table``
        is an index over six core packages, and one of them
        (``core/services/engine_config``) reaches ``bot_service``, which pulls in
        the DI container at import time — so a module-level import here closes a
        cycle and fails with a partially-initialised ``BotService``. That
        package's own ``__init__`` records the same hazard about re-exporting its
        submodules, and ``schema/entries.py`` imports ``core/services/identity``
        this way for exactly this reason. ``coords`` stays a module-level import
        because it is a genuine leaf: a frozen dataclass and nothing else.
        """
        from agentclaw.community.core.bot_config_surface.table import CONFIG_SURFACE

        return CONFIG_SURFACE[category].from_record(
            self.bot_id, self.owner_id, **extra
        )


__all__ = ["ApplyContext"]
