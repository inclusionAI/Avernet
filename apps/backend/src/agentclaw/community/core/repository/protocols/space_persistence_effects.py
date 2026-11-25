"""Repository-internal, same-transaction Space extensions; never a remote API."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from agentclaw.community.core.spaces.repository.models import SpaceModel


@runtime_checkable
class SpacePersistenceEffects(Protocol):
    @abstractmethod
    def lock_space(
        self, session: Session, *, space_id: int, environment: str
    ) -> SpaceModel | None: ...

    @abstractmethod
    def record_membership(
        self, session: Session, space: SpaceModel, *, disabled: bool = False
    ) -> None: ...

    @abstractmethod
    def reserve_identity(self, session: Session, space: SpaceModel) -> None: ...
