"""Canonicalize legacy resource addresses inside an existing ORM session.

Some administrative stores also accept offline records whose Bot no longer
exists. Keep that historical address when there is no live match; never choose
between two different live Bots. This helper does not grant user access.
"""

from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import Session

from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env


def resource_locator(session: Session, bot_id: str, locator: str) -> str:
    """Return the immutable entity for an existing Bot, or the offline locator."""
    rows = (
        session.query(BotModel.entity_id)
        .filter(
            BotModel.bot_id == bot_id,
            or_(BotModel.entity_id == locator, BotModel.owner_id == locator),
            BotModel.env == get_current_env(),
            BotModel.is_delete == 0,
        )
        .limit(2)
        .all()
    )
    if len(rows) > 1:
        raise BotLookupAmbiguousError(f"Ambiguous resource Bot locator: {bot_id}")
    if not rows:
        return locator
    entity_id = rows[0][0]
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise ValueError("Bot entity_id is required for resource addressing")
    return entity_id


def resource_owner_filter(bot_column, locator_column, owner_id: str):
    """Admin filters accept an entity locator or a current responsible owner.

    This is a set-based filter, not user authorization. The tenant ORM guard
    applies to the Bot subquery as well as to the outer resource query.
    """
    bots = select(BotModel.bot_id, BotModel.entity_id).where(
        BotModel.owner_id == owner_id,
        BotModel.env == get_current_env(),
        BotModel.is_delete == 0,
    )
    return or_(locator_column == owner_id, tuple_(bot_column, locator_column).in_(bots))
