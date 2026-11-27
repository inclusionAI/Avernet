from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentswarm.domain.models import (
    AgentDefinition, AgentDefinitionId, AgentDefinitionPage, TenantId,
)


@runtime_checkable
class AgentDefinitionRepositoryProtocol(Protocol):
    """Persistence for the versioned agent definition.

    Every read and list predicate carries ``owner_id`` *and* the live
    ``is_deleted = 0`` in the SQL rather than post-filtering, so another user's
    definition is never even loaded.
    """

    def create(self, definition: AgentDefinition) -> AgentDefinition: ...

    def get_live(self, definition_id: AgentDefinitionId, tenant: TenantId,
                 owner_id: str) -> AgentDefinition | None: ...

    def get_row(self, row_id: int, tenant: TenantId,
                owner_id: str) -> AgentDefinition | None: ...

    def get_many_live(self, definition_ids: list[AgentDefinitionId],
                      tenant: TenantId,
                      owner_id: str) -> list[AgentDefinition]: ...

    def list_live_page(self, tenant: TenantId, owner_id: str,
                       name_filter: str | None = None,
                       page: int = 1, page_size: int = 20
                       ) -> AgentDefinitionPage: ...

    def list_versions(self, definition_id: AgentDefinitionId, tenant: TenantId,
                      owner_id: str) -> list[AgentDefinition]: ...

    def replace_live(self, definition: AgentDefinition,
                     expected_row_id: int) -> AgentDefinition: ...

    def retire_live(self, definition_id: AgentDefinitionId, tenant: TenantId,
                    owner_id: str) -> int: ...
