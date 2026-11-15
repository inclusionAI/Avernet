"""Bounded, set-based candidates for display metadata compatibility locators."""

from typing import Any

from sqlalchemy import and_, case, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


def metadata_locator_candidates(
    session: Session, pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Read at most two candidates per pair in one ORM SELECT.

    Exact owner/entity matches sort first. Two rows suffice to distinguish
    a unique direct match, an ambiguous direct match, and a unique bare-ID
    fallback. Default Bots only participate through exact locators. The caller
    still owns ambiguity rejection and validation of retained BCN references.
    """
    pairs = list(dict.fromkeys(pairs))
    if not pairs:
        return {}
    if len(pairs) > 100:
        raise ValueError("metadata locator lookup accepts at most 100 pairs")
    requested = union_all(*[
        select(literal(index).label("position"), literal(bot_id).label("bot_id"),
               literal(locator).label("locator"))
        for index, (bot_id, locator) in enumerate(pairs)
    ]).subquery("metadata_requested")
    exact = or_(BotModel.owner_id == requested.c.locator,
                BotModel.entity_id == requested.c.locator)
    ranked = session.query(
        BotModel.id.label("bot_pk"), requested.c.position,
        case((exact, True), else_=False).label("exact_match"),
        func.max(case((exact, 1), else_=0)).over(
            partition_by=requested.c.position,
        ).label("has_exact"),
        func.row_number().over(
            partition_by=requested.c.position,
            order_by=(case((exact, 0), else_=1), BotModel.id),
        ).label("candidate_rank"),
    ).join(requested, and_(
        BotModel.bot_id == requested.c.bot_id,
        or_(requested.c.bot_id != "default", exact),
    )).filter(
        BotModel.is_delete == 0, BotModel.env == get_current_env(),
        # Scope BEFORE ranking as well as through the outer ORM tenant guard:
        # another tenant's rows must never consume this pair's two slots.
        BotModel.avernet_tenant == get_current_avernet_tenant(),
    ).subquery("metadata_candidates")
    rows = session.query(BotModel, ranked.c.position, ranked.c.exact_match).join(
        ranked, ranked.c.bot_pk == BotModel.id,
    ).filter(
        ranked.c.candidate_rank <= 2,
        or_(ranked.c.exact_match, ranked.c.has_exact == 0),
    ).all()
    candidates: dict[tuple[str, str], list[dict[str, Any]]] = {pair: [] for pair in pairs}
    for bot, position, exact_match in rows:
        candidates[pairs[position]].append({
            **bot.to_dict(), "_metadata_locator_match": bool(exact_match),
        })
    return candidates
