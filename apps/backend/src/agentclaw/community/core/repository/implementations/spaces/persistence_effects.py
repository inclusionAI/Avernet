"""Default Space persistence: no knowledge storage, capture, or identity policy."""

from sqlalchemy.orm import Session

from agentclaw.community.core.repository.implementations.spaces.source_lock import (
    lock_source_space,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel


class NoKnowledgeSpacePersistenceEffects:
    def lock_space(
        self, session: Session, *, space_id: int, environment: str
    ) -> SpaceModel | None:
        return lock_source_space(
            session,
            space_id=space_id,
            environment=environment,
            sqlite_write_guard=False,
        )

    def record_membership(
        self, session: Session, space: SpaceModel, *, disabled: bool = False
    ) -> None:
        pass

    def reserve_identity(self, session: Session, space: SpaceModel) -> None:
        pass
