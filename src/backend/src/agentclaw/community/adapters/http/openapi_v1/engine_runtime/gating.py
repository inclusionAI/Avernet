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
    relay: EngineRuntimeRelayProtocol,
    bot_id: str,
    *,
    caller_id: str,
    owner_id: str,
    stage: str,
    surface: str,
) -> BotFacts:
    """Resolve the addressed bot and reject a caller who may not operate it.

    Runs **before** any device call, deliberately: a filter applied to what the
    device returned would already have fetched device-wide data. This performs
    the ``(bot_id, owner_id)`` resolve and the operator adjudication, so a
    bot that does not exist under the named owner — or a caller who may not
    operate it — raises ``BotNotFoundError`` here, before a device is
    touched, byte-identical either way. The resolved facts are returned so
    the forward can reuse them (``relay.call(..., facts=facts, stage=…)``)
    instead of resolving again.

    ``caller_id`` is the request's verified user (``UserIdDep``);
    ``owner_id`` is the owner it addresses (``OwnerIdDep`` — the caller when
    unnamed). ``surface`` names the group for the refusal message; the
    adapter maps the type refusal to a public 501 — what the surface cannot
    serve, rather than something the caller may retry or fix.
    """
    facts = await relay.resolve_bot_off_loop(bot_id, owner_id, caller_id)
    require_operable_bot(facts.bot_type, stage=stage, surface=surface)
    return facts


__all__ = ["resolve_operable_bot"]
