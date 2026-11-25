"""Internal producer precondition, evaluated in its existing transaction."""

from typing import Protocol

from sqlalchemy.orm import Session


class SpaceKnowledgeCaptureGuard(Protocol):
    def require_enabled(self, session: Session) -> None: ...
