"""Opt-in knowledge capture in the source writer's existing ORM transaction."""

from sqlalchemy.orm import Session

from agentclaw.community.core.repository.implementations.spaces.identity import (
    reserve_new_space_identity,
)
from agentclaw.community.core.repository.implementations.spaces.knowledge_sync import (
    record_membership_snapshot,
)
from agentclaw.community.core.repository.implementations.spaces.source_lock import (
    lock_source_space,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel


class KnowledgeSpacePersistenceEffects:
    def lock_space(
        self, session: Session, *, space_id: int, environment: str
    ) -> SpaceModel | None:
        return lock_source_space(
            session, space_id=space_id, environment=environment, sqlite_write_guard=True
        )

    def record_membership(
        self, session: Session, space: SpaceModel, *, disabled: bool = False
    ) -> None:
        record_membership_snapshot(session, space, disabled=disabled)

    def reserve_identity(self, session: Session, space: SpaceModel) -> None:
        reserve_new_space_identity(session, space)
