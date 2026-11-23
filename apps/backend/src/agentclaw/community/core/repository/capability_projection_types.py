"""Value types for a Bot's versioned capability reconciliation state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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
