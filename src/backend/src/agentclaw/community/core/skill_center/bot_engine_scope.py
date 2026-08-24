"""How a Bot's engine scopes the SkillSets a *read* should consider.

Not shared with ``SkillSetManagementService``, whose identically-shaped
helpers feed writes: there a missing engine must narrow the scope to nothing
rather than widen it.
"""

from __future__ import annotations

from collections.abc import Mapping

from agentclaw.community.core.workspace.skill_layout import (
    runtime_layout_engine_for_bot,
)


def bot_engine_type(bot: Mapping[str, object]) -> str | None:
    """The Bot's engine, or ``None`` — meaning "do not filter Sets by engine".

    ``None`` rather than the string ``"None"``, which matches no Set and would
    empty the answer instead of widening it.
    """
    return str(bot.get("active_engine") or "") or None


def bot_default_engine_types(bot: Mapping[str, object]) -> tuple[str, ...]:
    """Default-Set precedence: runtime layout engine, then the persisted one.

    A coding template runs in an AICoding image while staying ``claude_code``
    logically, so the filesystem identity is tried first.
    """
    engine = bot_engine_type(bot)
    layout = str(runtime_layout_engine_for_bot(bot) or "") or None
    return tuple(dict.fromkeys(c for c in (layout, engine) if c))
