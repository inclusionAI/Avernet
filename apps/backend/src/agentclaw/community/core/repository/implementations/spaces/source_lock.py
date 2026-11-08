"""Source-only Space row guard; does not depend on knowledge storage."""

from sqlalchemy.orm import Session

from agentclaw.community.core.spaces.repository.models import SpaceModel


def lock_source_space(
    db: Session, *, space_id: int, environment: str, sqlite_write_guard: bool
) -> SpaceModel | None:
    query = db.query(SpaceModel).filter(
        SpaceModel.id == space_id,
        SpaceModel.env == environment,
        SpaceModel.deleted_at.is_(None),
    )
    if sqlite_write_guard and db.get_bind().dialect.name == "sqlite":
        # SQLite ignores FOR UPDATE. Acquire the cross-process write reservation
        # before a knowledge-enabled source/roster decision, preserving onupdate.
        query.update(
            {
                SpaceModel.id: SpaceModel.id,
                SpaceModel.gmt_modified: SpaceModel.gmt_modified,
            },
            synchronize_session=False,
        )
    return query.with_for_update().one_or_none()
