"""Service API for Space Skill Publication resources and commands."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.publication_contract import (
    PublicationAttemptRecord,
    PublicationImpactItem,
    PublicationRetryResult,
)


@runtime_checkable
class SpaceSkillPublicationServiceProtocol(Protocol):
    def list_publication_impact(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        page: int,
        page_size: int,
    ) -> tuple[int, list[PublicationImpactItem]]: ...

    def create_publication(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        request_id: str,
    ) -> PublicationAttemptRecord: ...

    def list_publications(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        page: int,
        page_size: int,
    ) -> tuple[int, list[PublicationAttemptRecord]]: ...

    def get_publication(
        self,
        *,
        space_id: int,
        skill_id: int,
        attempt_id: int,
        actor_id: str,
    ) -> PublicationAttemptRecord: ...

    def retry_publication(
        self,
        *,
        space_id: int,
        skill_id: int,
        attempt_id: int,
        actor_id: str,
    ) -> PublicationRetryResult: ...


__all__ = ["SpaceSkillPublicationServiceProtocol"]
