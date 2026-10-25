"""Persistence contracts for spaces and space membership."""

from __future__ import annotations

from abc import abstractmethod
from typing import ContextManager, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.plugin_api.space_membership import SpaceMembershipSnapshot
    from agentclaw.community.core.spaces.management_models import (
        ManagedSpace,
        MemberPageQuery,
        SpacePageQuery,
    )
    from agentclaw.community.core.spaces.models import (
        SpaceListScope,
        PersonalSpaceLookupRecord,
        SpaceMemberRecord,
        SpaceMemberSummaryRecord,
        SpaceRecord,
        SpaceRole,
        SpaceSummaryRecord,
    )


@runtime_checkable
class SpaceRepositoryProtocol(Protocol):
    @abstractmethod
    def initialize_personal(
        self, *, user_id: str, env: str
    ) -> tuple[SpaceRecord, bool]: ...

    @abstractmethod
    def get_personal_space(self, *, user_id: str, env: str) -> SpaceRecord | None: ...

    @abstractmethod
    def create_personal_transaction(
        self, *, user_id: str, creator_user_name: str | None, env: str
    ) -> ContextManager[SpaceRecord]: ...

    @abstractmethod
    def personal_sc_team_binding_transaction(
        self, *, space_id: int, env: str
    ) -> ContextManager[SpaceRecord]: ...

    @abstractmethod
    def get_team_space_by_name(
        self, *, creator_id: str, name: str, env: str
    ) -> SpaceRecord | None: ...

    @abstractmethod
    def create_team_transaction(
        self,
        *,
        name: str,
        creator_id: str,
        creator_user_name: str | None,
        env: str,
        initial_members: dict[str, str | None] | None = None,
    ) -> ContextManager[SpaceRecord]: ...

    @abstractmethod
    def get_space(self, *, space_id: int, env: str) -> SpaceRecord | None: ...

    @abstractmethod
    def backfill_sc_team_id(
        self, *, space_id: int, env: str, sc_team_id: str
    ) -> bool: ...

    @abstractmethod
    def get_space_by_code(self, *, space_code: str, env: str) -> SpaceRecord | None: ...

    @abstractmethod
    def batch_query_personal(
        self, *, user_ids: list[str], env: str
    ) -> list[PersonalSpaceLookupRecord]: ...

    @abstractmethod
    def list_spaces(
        self,
        *,
        user_id: str,
        env: str,
        keyword: str | None,
        space_type: str | None,
        offset: int,
        limit: int,
        scope: SpaceListScope = "all",
    ) -> tuple[int, list[SpaceSummaryRecord]]: ...

    @abstractmethod
    def get_member(
        self, *, space_id: int, user_id: str, env: str
    ) -> SpaceMemberRecord | None: ...

    @abstractmethod
    def list_members(
        self,
        *,
        space_id: int,
        env: str,
        keyword: str | None,
        offset: int,
        limit: int,
    ) -> tuple[int, list[SpaceMemberSummaryRecord]]: ...

    @abstractmethod
    def add_member(
        self,
        *,
        space_id: int,
        user_id: str,
        user_name: str | None = None,
        role: SpaceRole,
        creator_id: str,
        env: str,
    ) -> SpaceMemberRecord: ...

    @abstractmethod
    def delete_member(self, *, space_id: int, user_id: str, env: str) -> bool: ...

    @abstractmethod
    def update_member_role(
        self, *, space_id: int, user_id: str, role: SpaceRole, env: str
    ) -> SpaceMemberRecord | None: ...


@runtime_checkable
class SpaceManagementRepositoryProtocol(Protocol):
    """Maintenance persistence; every read/write must filter the explicit env.

    Batch operations lock the parent, check existence and commit atomically.
    Missing relations are no-ops on remove, but reject an entire role batch.
    """

    @abstractmethod
    def list_teams(self, *, query: SpacePageQuery, env: str) -> dict: ...
    @abstractmethod
    def get_team(self, *, space_id: int, env: str) -> ManagedSpace: ...
    @abstractmethod
    def rename_team(
        self, *, space_id: int, name: str, actor_id: str, env: str
    ) -> dict: ...
    @abstractmethod
    def delete_team(self, *, space_id: int, with_members: bool, env: str) -> dict: ...
    @abstractmethod
    def list_members(
        self, *, space_id: int, query: MemberPageQuery, env: str
    ) -> dict: ...
    @abstractmethod
    def add_members(
        self, *, space_id: int, members: dict[str, str | None], actor_id: str, env: str
    ) -> dict: ...
    @abstractmethod
    def remove_members(
        self, *, space_id: int, user_ids: list[str], env: str
    ) -> dict: ...
    @abstractmethod
    def set_member_roles(
        self, *, space_id: int, user_ids: list[str], role: str, env: str
    ) -> dict: ...


@runtime_checkable
class SpaceKnowledgeSyncRepositoryProtocol(Protocol):
    """Ordered immutable membership delivery, scoped to a fixed env and app."""

    @abstractmethod
    def activate(self, *, space_id: int, environment: str, app: str) -> None: ...

    @abstractmethod
    def progress(
        self, *, space_id: int, environment: str, app: str
    ) -> tuple[int, int, str] | None: ...

    @abstractmethod
    def next_snapshot(
        self, *, space_id: int, environment: str, app: str
    ) -> SpaceMembershipSnapshot | None: ...

    @abstractmethod
    def acknowledge(self, *, snapshot: SpaceMembershipSnapshot, app: str) -> None: ...

    @abstractmethod
    def repair_pending(
        self, *, environment: str, app: str, after_space_id: int, limit: int
    ) -> int: ...
