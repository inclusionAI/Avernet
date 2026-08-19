"""Persistence contracts for spaces and space membership."""

from __future__ import annotations

from abc import abstractmethod
from typing import ContextManager, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.spaces.models import (
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
    def create_team_transaction(
        self, *, name: str, creator_id: str, env: str
    ) -> ContextManager[SpaceRecord]: ...

    @abstractmethod
    def get_space(self, *, space_id: int, env: str) -> SpaceRecord | None: ...

    @abstractmethod
    def get_space_summary(
        self, *, space_id: int, user_id: str, env: str
    ) -> SpaceSummaryRecord | None: ...

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
