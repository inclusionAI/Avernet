"""The resolve-and-gate step every engine-runtime group runs before forwarding.

One helper, used by the sessions, engine, models, nodes and approvals routers (the
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
    relay: EngineRuntimeRelayProtocol,
    bot_id: str,
    *,
    caller_id: str,
    owner_id: str,
    stage: str,
    surface: str,
) -> BotFacts:
    """Resolve the addressed bot and reject a caller who may not operate it.

    Runs before any device call. ``owner_id`` is the compatibility locator
    from ``OwnerIdDep`` and can name a permanent entity or current Owner.
    The relay resolves a unique scoped Bot and authorizes ``caller_id`` from
    the verified principal against its current Owner/collaborator roles.
    Missing Bots and unauthorized callers both receive a masked 404.

    The returned facts carry the current Owner and are reused by the forward;
    the raw locator must not be used as a personnel identity. The relay retains
    its own operator check even when an upstream access seam also authorizes
    the request. ``surface`` selects the unsupported-type refusal message.
    """
    facts = await relay.resolve_bot_off_loop(bot_id, owner_id, caller_id)
    require_operable_bot(facts.bot_type, stage=stage, surface=surface)
    return facts


__all__ = ["resolve_operable_bot"]
