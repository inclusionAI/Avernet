"""Public Service API for permanent Space Skill Draft Edit Leases."""

from __future__ import annotations

from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.skill_center_types import (
        DraftEditLeaseViewRecord,
    )


@runtime_checkable
class DraftEditLeaseServiceProtocol(Protocol):
    def get_lease(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> DraftEditLeaseViewRecord: ...

    def acquire(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> DraftEditLeaseViewRecord: ...

    def release(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        fencing_token: int,
    ) -> DraftEditLeaseViewRecord: ...

    def takeover(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> DraftEditLeaseViewRecord: ...

__all__ = ["DraftEditLeaseServiceProtocol"]
