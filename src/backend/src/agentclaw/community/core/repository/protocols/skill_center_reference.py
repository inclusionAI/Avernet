"""Repository contract for SC Public Reference operations."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.repository.skill_center_reference_types import (
        MaterializedPublicCenterAsset,
        PublicCenterVersionTarget,
        SkillCenterReferenceWorkBatch,
        SkillCenterReferenceWorkItem,
    )
    from agentclaw.community.core.skill_center.reference_contract import (
        SkillCenterReferenceBatch,
        SkillCenterReferenceCreateResult,
        SkillCenterReferenceItem,
        SkillCenterReferenceStatus,
    )


@runtime_checkable
class SkillCenterReferenceRepositoryProtocol(Protocol):
    @abstractmethod
    def get_batch_by_idempotency_key(
        self, *, env: str, idempotency_key: str
    ) -> tuple[SkillCenterReferenceBatch, str] | None: ...

    @abstractmethod
    def create_or_get_batch(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_set_id: str,
        actor_id: str,
        idempotency_key: str,
        request_hash: str,
        skill_codes: tuple[str, ...],
        request_id: str,
        reference_ids: tuple[str, ...],
    ) -> SkillCenterReferenceCreateResult: ...

    @abstractmethod
    def get_work_batch(
        self, *, env: str, request_id: str
    ) -> SkillCenterReferenceWorkBatch | None: ...

    @abstractmethod
    def update_item(
        self,
        *,
        env: str,
        reference_id: str,
        status: SkillCenterReferenceStatus,
        **fields: object,
    ) -> SkillCenterReferenceWorkItem: ...

    @abstractmethod
    def ensure_public_version(
        self,
        *,
        env: str,
        actor_id: str,
        locator: str,
        skill_uuid: str,
        skill_name: str,
        description: str | None,
        sc_skill_id: int,
        sc_version_number: str,
        sc_version_id: int,
    ) -> PublicCenterVersionTarget: ...

    @abstractmethod
    def list_items(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_set_id: str,
        request_id: str | None,
        status: SkillCenterReferenceStatus | None,
        offset: int,
        limit: int,
    ) -> tuple[int, tuple[SkillCenterReferenceItem, ...]]: ...

    @abstractmethod
    def get_item(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_set_id: str,
        reference_id: str,
    ) -> SkillCenterReferenceItem | None: ...

    @abstractmethod
    def list_materialized_public_assets(
        self, *, env: str
    ) -> tuple[MaterializedPublicCenterAsset, ...]: ...


__all__ = [
    "SkillCenterReferenceRepositoryProtocol",
]
