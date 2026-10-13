"""Persistence boundary for stable Local Skill security observations."""

from __future__ import annotations

from abc import abstractmethod
from threading import Event
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.skill_center.local_skill_security_observation import (
        LocalSkillSecurityHandoffAttempt,
        LocalSkillSecurityIdentity,
        LocalSkillSecurityObservation,
    )


class ObservationHandoffCancelledError(Exception):
    """Timed-out upload may not select an obsolete current observation."""


class AssetIncarnationMismatchError(ValueError):
    """A deleted/replaced formal asset may not claim an old security handoff."""


@runtime_checkable
class LocalSkillSecurityObservationRepositoryProtocol(Protocol):
    @abstractmethod
    def record_asset_deleted(
        self, *, env: str, bot_id: str, owner_id: str,
        skill_id: int, actor_id: str,
    ) -> LocalSkillSecurityIdentity | None:
        """Persist a tombstone after formal deletion for independent sync."""
        ...

    @abstractmethod
    def get_latest_handoff_for_asset(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_id: int,
        incarnation_id: str,
    ) -> LocalSkillSecurityHandoffAttempt | None:
        """Latest persisted handoff for the current security identity."""
        ...

    @abstractmethod
    def reserve_handoff(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        skill_id: int,
        observation_id: str,
        sha256: str,
        snapshot_ref: str,
        cancelled: Event,
        name: str,
        description: str,
        creator_id: str | None = None,
        incarnation_id: str,
    ) -> int:
        """Reserve a scan row before storage I/O; abort if timed out."""
        ...

    @abstractmethod
    def get_handoff_failure(
        self, *, env: str, observation_id: str
    ) -> LocalSkillSecurityHandoffAttempt | None:
        """Fetch an exact upload's failed handoff for controlled repair."""
        ...

    @abstractmethod
    def record_handoff_failure(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        skill_id: int,
        observation_id: str,
        sha256: str,
        snapshot_ref: str,
        stage: str,
        snapshot_verified: bool,
        name: str | None = None,
        description: str | None = None,
        creator_id: str | None = None,
        upload_order: int | None = None,
        incarnation_id: str,
    ) -> None:
        """Retain an exact-upload failure for controlled reconciliation."""
        ...

    @abstractmethod
    def ensure_for_asset(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_id: int,
        creator_id: str | None,
        incarnation_id: str,
    ) -> LocalSkillSecurityIdentity: ...

    @abstractmethod
    def get_identity_for_asset(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_id: int,
        incarnation_id: str,
    ) -> LocalSkillSecurityIdentity | None: ...

    @abstractmethod
    def get_formal_incarnation_id(
        self, *, env: str, bot_id: str, owner_id: str, skill_id: int
    ) -> str | None:
        """Read the current Local asset's recorded security identity."""
        ...

    @abstractmethod
    def record_observation(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_id: int,
        observation_id: str,
        sha256: str,
        snapshot_ref: str,
        modifier_id: str,
        name: str,
        description: str,
        upload_order: int,
        cancelled: Event | None = None,
        incarnation_id: str,
    ) -> LocalSkillSecurityObservation: ...

    @abstractmethod
    def get_current_for_asset(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_id: int,
        incarnation_id: str,
    ) -> LocalSkillSecurityObservation | None: ...

    @abstractmethod
    def get_by_observation_id(
        self, *, env: str, observation_id: str
    ) -> LocalSkillSecurityObservation | None: ...

    @abstractmethod
    def list_for_asset(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_id: int,
        incarnation_id: str,
    ) -> tuple[LocalSkillSecurityObservation, ...]: ...

    @abstractmethod
    def page_for_asset(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_id: int,
        page: int,
        page_size: int,
        incarnation_id: str,
    ) -> tuple[int, tuple[LocalSkillSecurityObservation, ...]]: ...

    @abstractmethod
    def save_external_artifact(
        self,
        *,
        env: str,
        observation_id: str,
        artifact: dict[str, Any],
    ) -> LocalSkillSecurityObservation: ...

    @abstractmethod
    def save_check_submission(
        self,
        *,
        env: str,
        observation_id: str,
        check_id: str,
    ) -> LocalSkillSecurityObservation: ...

    @abstractmethod
    def claim_scan_submission(
        self,
        *,
        env: str,
        observation_id: str,
    ) -> bool:
        """Atomically fence the one non-idempotent provider submit attempt."""
        ...

    @abstractmethod
    def save_report(
        self,
        *,
        env: str,
        observation_id: str,
        check_id: str,
        risk_status: str,
        report: dict[str, Any],
        detail_url: str | None,
    ) -> LocalSkillSecurityObservation: ...

    @abstractmethod
    def save_in_progress_report(
        self,
        *,
        env: str,
        observation_id: str,
        check_id: str,
        risk_status: str,
        report: dict[str, Any],
        detail_url: str | None,
    ) -> LocalSkillSecurityObservation:
        """Retain the provider's nonterminal DETECTING fact without closing scan."""
        ...

    @abstractmethod
    def mark_scan_error(
        self,
        *,
        env: str,
        observation_id: str,
        error_code: str,
        error_message: str,
    ) -> LocalSkillSecurityObservation: ...

    @abstractmethod
    def mark_submit_unknown(
        self,
        *,
        env: str,
        observation_id: str,
        error_code: str,
        error_message: str,
    ) -> LocalSkillSecurityObservation: ...

    @abstractmethod
    def mark_sync_completed(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_id: int,
        observation_id: str,
        incarnation_id: str,
    ) -> bool: ...

    @abstractmethod
    def mark_sync_error(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_id: int,
        observation_id: str,
        error_code: str,
        error_message: str,
        incarnation_id: str,
    ) -> bool: ...

    @abstractmethod
    def list_recoverable_scans(
        self,
        *,
        env: str,
        after_observation_id: str | None,
        limit: int,
    ) -> tuple[LocalSkillSecurityObservation, ...]:
        """Page unfinished scans safe to enqueue, excluding ambiguous submits."""
        ...

    @abstractmethod
    def list_pending_sync_identities(
        self,
        *,
        env: str,
        after_provider_skill_id: str | None,
        limit: int,
    ) -> tuple[LocalSkillSecurityIdentity, ...]:
        """Page identities whose newest successful upload has not been synced."""
        ...

    @abstractmethod
    def list_recovery_tenants(self, *, env: str) -> tuple[str, ...]:
        """Background-only cross-tenant discovery for durable security recovery."""
        ...

    @abstractmethod
    def list_repairable_handoffs(
        self, *, env: str, after_observation_id: str | None, limit: int
    ) -> tuple[LocalSkillSecurityHandoffAttempt, ...]:
        """Verified identity handoffs not yet enrolled as observations."""
        ...

    @abstractmethod
    def reopen_verified_handoff(
        self, *, env: str, observation_id: str, verified_sha256: str
    ) -> bool:
        """Confirm readback digest and resume an abandoned frozen handoff."""
        ...


__all__ = [
    "LocalSkillSecurityObservationRepositoryProtocol",
    "ObservationHandoffCancelledError",
    "AssetIncarnationMismatchError",
]
