"""Versioned persistence for post-commit Bot capability projection."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Sequence

if TYPE_CHECKING:
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        ProjectionScope,
        RuntimeProjectionResult,
    )
    from agentclaw.community.core.skills_pool.models import PoolSkillMapping


@dataclass(frozen=True, slots=True)
class CapabilityProjectionTarget:
    bot_pk: int
    bot_id: str
    entity_id: str
    tenant: str
    env: str


@dataclass(frozen=True, slots=True)
class CapabilityProjectionState(CapabilityProjectionTarget):
    owner_id: str
    binding_id: int | None
    device_id: str | None
    engine_type: str
    version: int
    scope: ProjectionScope
    retired_mappings: tuple[PoolSkillMapping, ...]
    result: RuntimeProjectionResult
    write_blocked: bool
    is_current_binding: bool
    retry_pending: bool

    @property
    def needs_recovery(self) -> bool:
        return not self.is_current_binding or (self.retry_pending and not self.write_blocked)

    @property
    def revision(self) -> str:
        return f"{self.bot_pk}/{self.version}"


class CapabilityProjectionRepositoryProtocol(Protocol):
    @abstractmethod
    def validate_schema(self) -> None:
        """Verify the additive migration before a worker serves capability writes."""
        ...

    @abstractmethod
    def read(self, *, bot_id: str, entity_id: str) -> CapabilityProjectionState | None:
        """Report the current target's status without creating an obligation."""
        ...

    @abstractmethod
    def require(
        self, *, bot_id: str, entity_id: str, scope: ProjectionScope,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> CapabilityProjectionState:
        """Advance the projection revision and merge a new obligation."""
        ...

    @abstractmethod
    def prepare(self, *, bot_id: str, entity_id: str) -> CapabilityProjectionState | None:
        """Refresh any existing obligation for the live Bot's binding."""
        ...

    @abstractmethod
    def begin(self, state: CapabilityProjectionState) -> bool:
        """Fence a dispatch for exactly this revision and binding."""
        ...

    @abstractmethod
    def complete(
        self, state: CapabilityProjectionState, result: RuntimeProjectionResult, *,
        write_blocked: bool = False,
    ) -> bool:
        """Confirm only the sent revision. Keep ambiguous same-target writes fenced."""
        ...

    @abstractmethod
    def due(
        self, *, env: str, limit: int, after_bot_pk: int = 0,
    ) -> tuple[CapabilityProjectionTarget, ...]:
        """Read recovery addresses only across tenants in one data environment."""
        ...
