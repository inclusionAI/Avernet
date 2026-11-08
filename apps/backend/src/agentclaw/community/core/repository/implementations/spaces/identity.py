"""Keep SQLite's reusable rowids out of retained membership-delivery scopes."""

from sqlalchemy.orm import Session

from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.core.space_knowledge.repository.models import (
    SpaceKnowledgeSyncModel,
)


def reserve_new_space_identity(db: Session, space: SpaceModel) -> None:
    """Called after INSERT, before any member, external effect or returned ID.

    The INSERT already holds SQLite's database write lock across processes.
    Its generated ID is above all live rows, but may reuse a deleted scope.
    Reserve above retained history in that environment without changing the
    source schema or removing pending disabled snapshots. Server databases keep
    their native sequences; no SQLite locking assumption is applied to them.
    """
    if db.get_bind().dialect.name != "sqlite":
        return
    historical = (
        db.query(SpaceKnowledgeSyncModel.space_id)
        .filter(
            SpaceKnowledgeSyncModel.env == space.env,
            SpaceKnowledgeSyncModel.space_id >= space.id,
        )
        .order_by(SpaceKnowledgeSyncModel.space_id.desc())
        .first()
    )
    if historical is not None:
        space.id = historical.space_id + 1
        db.flush()
