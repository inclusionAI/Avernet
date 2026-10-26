"""Repository-internal, same-transaction Space extensions; never a remote API."""

from typing import Protocol

from sqlalchemy.orm import Session

from agentclaw.community.core.spaces.repository.models import SpaceModel


class SpacePersistenceEffects(Protocol):
    def lock_space(
        self, session: Session, *, space_id: int, environment: str
    ) -> SpaceModel | None: ...

    def record_membership(
        self, session: Session, space: SpaceModel, *, disabled: bool = False
    ) -> None: ...

    def reserve_identity(self, session: Session, space: SpaceModel) -> None: ...
