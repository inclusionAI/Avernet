"""Service API contracts for spaces and members."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.spaces.models import (
        SpaceMemberRecord,
        SpaceMemberSummaryRecord,
        SpaceRecord,
        SpaceRole,
        SpaceSummaryRecord,
        SpaceType,
    )


@runtime_checkable
class SpaceServiceProtocol(Protocol):
    @abstractmethod
    def initialize_personal(self, *, user_id: str) -> tuple[SpaceRecord, bool]: ...

    @abstractmethod
    def create_team(self, *, name: str, creator_id: str) -> SpaceRecord: ...

    @abstractmethod
    def list_spaces(
        self,
        *,
        user_id: str,
        keyword: str | None,
        space_type: SpaceType | None,
        page_no: int,
        page_size: int,
    ) -> tuple[int, list[SpaceSummaryRecord]]: ...


@runtime_checkable
class SpaceMemberServiceProtocol(Protocol):
    @abstractmethod
    def list_members(
        self,
        *,
        space_id: int,
        actor_id: str,
        keyword: str | None,
        page_no: int,
        page_size: int,
    ) -> tuple[int, list[SpaceMemberSummaryRecord]]: ...

    @abstractmethod
    def add_member(
        self,
        *,
        space_id: int,
        actor_id: str,
        user_id: str,
        role: SpaceRole,
    ) -> SpaceMemberRecord: ...

    @abstractmethod
    def delete_member(self, *, space_id: int, actor_id: str, user_id: str) -> bool: ...

    @abstractmethod
    def update_role(
        self, *, space_id: int, actor_id: str, user_id: str, role: SpaceRole
    ) -> SpaceMemberSummaryRecord: ...
