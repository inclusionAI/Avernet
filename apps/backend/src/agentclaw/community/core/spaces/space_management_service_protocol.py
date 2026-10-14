"""Service API for adapter-authorized, environment-scoped maintenance.

No membership authorization, creator protection or last-admin retention applies.
All batch writes are atomic locally. Creation calls SC, but is not distributed
atomic or safe to retry blindly. Environment is resolved by the service, never
supplied by a request. Creation accepts only TEAM (the default when omitted);
other types fail before database writes or Skill Center calls. Existing page/OpenAPI governance is unchanged.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.spaces.management_models import (
        InitializeTeam,
        ManagedSpace,
        MemberBatch,
        MemberPageQuery,
        RenameTeam,
        SetMemberRoles,
        SpacePageQuery,
    )


@runtime_checkable
class SpaceManagementServiceProtocol(Protocol):
    @abstractmethod
    def create_team(self, command: InitializeTeam) -> ManagedSpace: ...
    @abstractmethod
    def list_teams(self, query: SpacePageQuery) -> dict: ...
    @abstractmethod
    def get_team(self, space_id: int) -> ManagedSpace: ...
    @abstractmethod
    def rename_team(
        self, space_id: int, command: RenameTeam, actor_id: str
    ) -> dict: ...
    @abstractmethod
    def delete_empty_team(self, space_id: int) -> dict: ...
    @abstractmethod
    def delete_team_with_members(self, space_id: int) -> dict: ...
    @abstractmethod
    def list_members(self, space_id: int, query: MemberPageQuery) -> dict: ...
    @abstractmethod
    def add_members(
        self, space_id: int, command: MemberBatch, actor_id: str
    ) -> dict: ...
    @abstractmethod
    def remove_members(self, space_id: int, command: MemberBatch) -> dict: ...
    @abstractmethod
    def set_member_roles(self, space_id: int, command: SetMemberRoles) -> dict: ...
