"""Safe compatibility addresses for Bot MCP configuration in the caller's Session.

The resource key is immutable entity_id. Only the uniquely attributable current
owner is a legacy alias; unknown historical owners are never guessed from bot_id.
"""

from sqlalchemy.orm import Session

from agentclaw.community.core.bot_management.errors import (
    BotAddressConflictError,
    BotLookupAmbiguousError,
)
from agentclaw.community.core.repository.implementations.bot.resource_locator import (
    resource_locator,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


def current_legacy_owner(
    session: Session, *, bot_id: str, owner_id: str, env: str
) -> str | None:
    bot = (
        session.query(BotModel)
        .filter(
            BotModel.avernet_tenant == get_current_avernet_tenant(),
            BotModel.env == env,
            BotModel.bot_id == bot_id,
            BotModel.entity_id == owner_id,
        )
        .order_by(BotModel.is_delete, BotModel.id.desc())
        .limit(2)
        .all()
    )
    if not bot:
        return None
    if (
        len(bot) > 1
        and bot[0].is_delete == bot[1].is_delete
        and bot[0].owner_id != bot[1].owner_id
    ):
        raise BotLookupAmbiguousError(f"Ambiguous MCP resource owner: {bot_id}")
    bot = bot[0]
    if bot.owner_id == owner_id:
        return None
    # Entity keys stay reserved after deletion: known foreign resources are not
    # compatibility aliases just because their spelling matches a human owner.
    foreign_resource = (
        session.query(BotModel.id)
        .filter(
            BotModel.avernet_tenant == get_current_avernet_tenant(),
            BotModel.env == env,
            BotModel.bot_id == bot_id,
            BotModel.entity_id == bot.owner_id,
        )
        .first()
    )
    if foreign_resource is not None:
        return None
    return str(bot.owner_id)


def require_same_bot(
    session: Session, *, bot_id: str, owner_id: str, legacy_owner: str, env: str
) -> None:
    if (
        resource_locator(session, bot_id, legacy_owner, include_deleted=True, env=env)
        != owner_id
    ):
        raise BotAddressConflictError(
            "Legacy MCP configuration does not identify the same Bot"
        )
