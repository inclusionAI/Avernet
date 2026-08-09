"""The resolve-and-gate step every engine-runtime group runs before forwarding.

One helper, used by the sessions, engine, models and approvals routers (the
connection endpoint runs the same gate inside ``EngineConnectionService``,
where its socket is composed). The rules themselves live in
``core/engine_runtime/gate.py``: who may operate a bot (its owner, or a
collaborator at member level or above — adjudicated inside the relay's
resolve), and which bot types the operator surfaces serve at all.
"""

from __future__ import annotations

from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.gate import require_operable_bot
from agentclaw.community.core.engine_runtime.models import BotFacts


async def resolve_operable_bot(
    relay: EngineRuntimeRelayProtocol, bot_id: str, owner_id: str, *, surface: str
) -> BotFacts:
    """Resolve the caller's bot and reject anything more than one caller reaches.

    Runs **before** any device call, deliberately: a filter applied to what the
    device returned would already have fetched device-wide data. This also
    performs the owner-scoped resolve, so a foreign ``bot_id`` raises
    ``BotNotFoundError`` here — before a device is touched. The resolved facts
    are returned so the forward can reuse them (``relay.call(..., facts=facts,
    draft_device=True)``) instead of resolving again.

    ``surface`` names the group for the refusal message; the adapter maps the
    type refusal to a public 501 — what the surface cannot serve, rather than
    something the caller may retry or fix.
    """
    facts = await relay.resolve_bot_off_loop(bot_id, owner_id, owner_id)
    require_operable_bot(facts.bot_type, surface=surface)
    return facts


__all__ = ["resolve_operable_bot"]
