"""Persistence contract for the Space Skill Publication aggregate."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.skill_center.publication_contract import (
        PublicationAttemptCreation,
        PublicationAttemptRecord,
        PublicationImpactCandidate,
        PublicationRecoveryKind,
        PublicationRetryResult,
        PublicationSubmissionClaim,
        PublicationWork,
    )


@runtime_checkable
class SpaceSkillPublicationRepositoryProtocol(Protocol):
    @abstractmethod
    def create_or_replay_attempt(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        request_id: str,
        env: str,
    ) -> PublicationAttemptCreation: ...

    @abstractmethod
    def get_attempt(
        self, *, space_id: int, skill_id: int, attempt_id: int, env: str
    ) -> PublicationAttemptRecord: ...

    @abstractmethod
    def list_attempts(
        self,
        *,
        space_id: int,
        skill_id: int,
        env: str,
        offset: int,
        limit: int,
    ) -> tuple[int, list[PublicationAttemptRecord]]: ...

    @abstractmethod
    def require_publisher(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> None: ...

    @abstractmethod
    def list_impact_candidates(
        self, *, skill_id: int, env: str
    ) -> tuple[PublicationImpactCandidate, ...]: ...

    @abstractmethod
    def get_work(self, *, attempt_id: int, env: str) -> PublicationWork: ...

    @abstractmethod
    def mark_prepared(
        self, *, attempt_id: int, package_url: str, env: str
    ) -> PublicationWork: ...

    @abstractmethod
    def claim_sc_submission(
        self, *, attempt_id: int, env: str
    ) -> PublicationSubmissionClaim: ...

    @abstractmethod
    def mark_waiting_sc(
        self, *, attempt_id: int, env: str
    ) -> PublicationAttemptRecord: ...

    @abstractmethod
    def mark_result_unknown(
        self,
        *,
        attempt_id: int,
        error_code: str,
        error_message: str,
        recovery_available: bool,
        env: str,
    ) -> PublicationAttemptRecord: ...

    @abstractmethod
    def mark_failed(
        self,
        *,
        attempt_id: int,
        error_code: str,
        error_message: str,
        env: str,
    ) -> PublicationAttemptRecord: ...

    @abstractmethod
    def begin_materialization(
        self,
        *,
        attempt_id: int,
        sc_skill_id: int,
        sc_version_id: int,
        sc_sha256: str | None,
        env: str,
    ) -> PublicationWork: ...

    @abstractmethod
    def complete_success(
        self,
        *,
        attempt_id: int,
        skill_version_id: int,
        env: str,
    ) -> PublicationAttemptRecord: ...

    @abstractmethod
    def mark_recovery_available(
        self,
        *,
        attempt_id: int,
        kind: PublicationRecoveryKind,
        error_code: str,
        error_message: str,
        env: str,
    ) -> PublicationAttemptRecord: ...

    @abstractmethod
    def restart_recovery(
        self,
        *,
        space_id: int,
        skill_id: int,
        attempt_id: int,
        actor_id: str,
        env: str,
    ) -> PublicationRetryResult: ...


__all__ = ["SpaceSkillPublicationRepositoryProtocol"]
