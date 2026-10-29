"""Resolve and lock a handoff's Space assignment in its caller's transaction."""

from sqlalchemy.orm import Session

from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.core.bot_management.owner_handoff.contracts import HandoffError
from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.spaces.repository.models import SpaceMemberModel, SpaceModel


def handoff_space_id(db: Session, bot: BotModel, new_owner_id: str) -> int | None:
    """Keep legacy NULL; move personal assignment; retain team membership scope."""
    if bot.space_id is None:
        return None
    source = (
        db.query(SpaceModel)
        .filter(
            SpaceModel.id == bot.space_id,
            SpaceModel.env == bot.env,
            SpaceModel.deleted_at.is_(None),
        )
        .with_for_update()
        .one_or_none()
    )
    if source is None:
        raise HandoffError("HANDOFF_SPACE_NOT_FOUND")
    target = source
    if source.space_type == SpaceType.PERSONAL.value:
        if source.personal_owner_id != bot.owner_id:
            raise HandoffError("HANDOFF_PERSONAL_SPACE_CONFLICT")
        target = (
            db.query(SpaceModel)
            .filter(
                SpaceModel.space_type == SpaceType.PERSONAL.value,
                SpaceModel.personal_owner_id == new_owner_id,
                SpaceModel.env == bot.env,
                SpaceModel.deleted_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        if target is None:
            raise HandoffError("HANDOFF_TARGET_SPACE_REQUIRED")
    elif source.space_type != SpaceType.TEAM.value:
        raise HandoffError("HANDOFF_SPACE_TYPE_INVALID")
    member = (
        db.query(SpaceMemberModel)
        .filter(
            SpaceMemberModel.space_id == target.id,
            SpaceMemberModel.user_id == new_owner_id,
            SpaceMemberModel.env == bot.env,
            SpaceMemberModel.status == "ACTIVE",
        )
        .with_for_update()
        .one_or_none()
    )
    if member is None:
        raise HandoffError("NEW_OWNER_SPACE_MEMBERSHIP_REQUIRED")
    return target.id
