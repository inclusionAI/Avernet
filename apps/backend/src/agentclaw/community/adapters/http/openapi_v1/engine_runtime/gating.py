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

    **This resolves the bot a second time, and that is a known cost.** The
    migrated engine-runtime rows carry ``Check(MEMBER)``, so ``bot_access``
    has already resolved ``(bot_id, owner_id)`` and adjudicated the caller
    before this runs. What the duplicate actually costs, per admitted request:

    - **Owner caller** — one extra ``BotService.get_bot``, which
      ``EngineRuntimeRelay.resolve_bot_off_loop`` documents as an owner-scoped
      row read plus a device-binding fetch plus a template fetch. No extra
      collaborator query: both level lookups short-circuit on
      ``user_id == owner_id`` before reaching that table.
    - **Collaborator caller** — the same, plus one collaborator-role query.

    Caching the seam's read would not remove it. The seam calls
    ``BotRepository.get_by_id_and_owner``; the relay needs the binding and
    template that only ``get_bot`` fetches, because :class:`BotFacts` is built
    from them. Handing the seam's record over saves the row read and leaves the
    other two.

    What *could* be skipped is the relay's ``require_bot_operator``, and it
    stays on purpose. A "the seam already checked" flag to skip it was
    considered and rejected — a bypass argument on an authorization function
    is the mechanism this feature exists to remove, and it would be spent to
    save one row read and, for collaborators, one indexed lookup.

    The redundancy therefore ends when the session rows migrate, not through a
    cache. Raised as a P2 in #1366 round 7.
    """
    facts = await relay.resolve_bot_off_loop(bot_id, owner_id, caller_id)
    require_operable_bot(facts.bot_type, stage=stage, surface=surface)
    return facts


__all__ = ["resolve_operable_bot"]
