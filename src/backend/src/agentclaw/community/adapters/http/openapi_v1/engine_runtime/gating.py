"""The resolve-and-gate step every engine-runtime group runs before forwarding.

One helper, used by the sessions, engine, models and approvals routers (the
connection endpoint runs the same gate inside ``EngineConnectionService``,
where its socket is composed). The rule itself lives in
``core/engine_runtime/gate.py``: these groups serve private personal bots and
a service bot's pre-publication **draft** workspace, and nothing any other
caller can reach.

The gate only holds together with how the groups forward — every
``relay.call`` in a gated group passes ``draft_device=True``, so a service
bot's request reaches the draft binding the owner alone uses, never the
published runtime, which is a multi-caller device whose data (sessions,
approvals) is not scoped per caller.
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

    ``surface`` names the group for the refusal message; the adapter maps both
    refusals to the same public 501 — what the surface cannot serve, rather
    than something the caller may retry or fix.
    """
    facts = await relay.resolve_bot_off_loop(bot_id, owner_id)
    require_operable_bot(facts.bot_type, is_shared=facts.is_shared, surface=surface)
    return facts


__all__ = ["resolve_operable_bot"]
