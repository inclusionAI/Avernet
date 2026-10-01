"""Service API for durable SC Public Reference operations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.reference_contract import (
    SkillCenterReferenceBatch,
    SkillCenterReferenceItem,
    SkillCenterReferencePage,
    SkillCenterReferenceStatus,
)


@runtime_checkable
class SkillCenterReferenceServiceProtocol(Protocol):
    def create(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        skill_set_id: str,
        idempotency_key: str,
        skill_codes: tuple[str, ...],
    ) -> SkillCenterReferenceBatch: ...

    def list(
        self,
        *,
        bot_id: str,
        owner_id: str,
        skill_set_id: str,
        request_id: str | None,
        status: SkillCenterReferenceStatus | None,
        page: int,
        page_size: int,
    ) -> SkillCenterReferencePage: ...

    def get(
        self,
        *,
        bot_id: str,
        owner_id: str,
        skill_set_id: str,
        reference_id: str,
    ) -> SkillCenterReferenceItem: ...


__all__ = ["SkillCenterReferenceServiceProtocol"]
