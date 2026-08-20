"""Consumer-side contracts for reusable Space authorization decisions."""

from __future__ import annotations

from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.spaces.models import (
        SpaceMemberRecord,
        SpaceRecord,
        SpaceRole,
    )


@runtime_checkable
class SpaceAccessServiceProtocol(Protocol):
    """Read and enforce live Space membership without importing its service."""

    def require_space(self, *, space_id: int) -> SpaceRecord: ...

    def require_space_reference(self, *, space_ref: str) -> SpaceRecord: ...

    def get_space_role(self, *, space_id: int, user_id: str) -> SpaceRole | None: ...

    def require_space_member(
        self, *, space_id: int, user_id: str
    ) -> tuple[SpaceRecord, SpaceMemberRecord]: ...
