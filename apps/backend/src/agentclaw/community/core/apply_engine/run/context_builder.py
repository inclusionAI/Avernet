"""Build the trusted identity and addressing for one Apply run."""

from typing import Optional

from agentclaw.community.core.apply_engine.capabilities import CapabilityResolver
from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


def _engine_and_bot_type(
    bot: Optional[dict],
    engine_type: Optional[str],
    bot_type: Optional[str],
) -> tuple[str, str]:
    """The two values every capability question is answered from.

    One helper because the record and record-free paths must not drift: a bot
    whose engine is read one way here and another way there would validate
    against one set of capabilities and apply against a different one.
    """
    if bot is not None:
        return str(bot.get("active_engine") or ""), str(bot.get("bot_type") or "")
    return str(engine_type or ""), str(bot_type or "")


def build_apply_context(
    capabilities: CapabilityResolver,
    *,
    bot_id: str,
    bot: Optional[dict],
    owner_id: str,
    actor_id: str,
    entity_id: str,
    env: str,
    apply_id: Optional[str] = None,
    engine_type: Optional[str] = None,
    bot_type: Optional[str] = None,
    permission_subject: str | None = None,
) -> ApplyContext:
    """The identity one apply runs under, with or without a bot record.

    ``bot`` is ``None`` on exactly one path: W13 runs the pre-container phase
    **before** the bot is created, so there is no record to read. That case
    supplies ``engine_type`` / ``bot_type`` from the creation request and
    resolves capabilities from those — which is the second entry point W1
    built for this caller, not a parallel implementation. ``ApplyContext.bot``
    is then a minimal stand-in; the two shipped materialisers do not read it,
    and the one that runs in this phase (``script``) reads only the engine,
    env and tenant.
    """
    engine, kind = _engine_and_bot_type(bot, engine_type, bot_type)
    # The stand-in the docstring describes, named rather than inlined so the
    # "no record yet" case reads as one thing.
    record = bot
    if record is None:
        record = {
            "bot_id": bot_id,
            "entity_id": entity_id,
            "active_engine": engine,
            "bot_type": kind,
        }
    return ApplyContext(
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
        entity_id=entity_id,
        env=env,
        tenant=get_current_avernet_tenant(),
        engine_type=engine,
        bot_type=kind,
        bot=record,
        capabilities=(
            capabilities.capabilities_for_bot(bot)
            if bot is not None
            else capabilities.resolve_capabilities(active_engine=engine, bot_type=kind)
        ),
        apply_id=apply_id,
        permission_subject=permission_subject,
    )
