"""Where one config category writes, addressed without reference to a request.

Four of the five categories on this surface need the same three values to reach
a bot's storage — ``entity_type``, ``entity_id``, ``engine_type`` — and today
each router computes them privately, in its own handler bodies, four different
ways. This is the type they all produce, so that the difference between them is
visible in one place rather than invisible in four.

**Two sources, one type.** A category resolves these from a bot record for a bot
that exists, and from the create request's parameters for one that does not yet
(W13 validates a manifest at preflight, before the bot record is written —
``core/bot_management/create_flow.py`` mints the id and the Passport identity in
its first phase and creates nothing). Both produce this, so a validator handed
one cannot tell which path it came from, which is what lets a single validation
path serve both entries.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BotConfigCoords:
    """One category's write address for one bot.

    ``engine_type`` is ``str | None`` with no default, and both are deliberate.
    ``identity`` and ``mcp`` address no engine at all, so there is no value to
    give them; defaulting it would hand them an engine they never had, and
    making it optional would let a category that *does* need one be constructed
    without it.
    """

    bot_id: str
    owner_id: str
    entity_type: str
    entity_id: str
    engine_type: str | None
