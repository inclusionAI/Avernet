"""Recovery-state persistence for Space Skill Publication Attempts."""

from __future__ import annotations

from agentclaw.community.core.skill_center.errors import (
    PublicationRecoveryNotAvailableError,
    PublicationRequiresNewAttemptError,
)
from agentclaw.community.core.skill_center.publication_contract import (
    PublicationAttemptRecord,
    PublicationRecoveryKind,
    PublicationRetryResult,
)


class SpaceSkillPublicationRecoveryMixin:
    """Keep retryable publication recovery transitions separate from publication I/O.

    Hosts provide the transactional database and the aggregate lock, scope, and
    record helpers. The mixin owns only recovery-state transitions.
    """

    _db: object

    def mark_recovery_available(
        self,
        *,
        attempt_id: int,
        kind: PublicationRecoveryKind,
        error_code: str,
        error_message: str,
        env: str,
    ) -> PublicationAttemptRecord:
        with self._db.transactional_orm_session() as session:
            attempt = self._attempt(session, attempt_id=attempt_id, env=env, lock=True)
            if attempt.status not in ("SUCCEEDED", "FAILED"):
                attempt.recovery_state = "AVAILABLE"
                attempt.recovery_kind = kind.value
                attempt.error_code = error_code
                attempt.error_message = error_message
                session.flush()
            return self._record(attempt)

    def record_materialization_failure(
        self,
        *,
        attempt_id: int,
        error_code: str,
        error_message: str,
        max_auto_retries: int,
        env: str,
    ) -> PublicationAttemptRecord:
        if max_auto_retries < 0:
            raise ValueError("max_auto_retries must not be negative")
        with self._db.transactional_orm_session() as session:
            attempt, _skill, version = self._lock_attempt_aggregate(
                session, attempt_id=attempt_id, env=env
            )
            if version is None or attempt.skill_version_id is None:
                raise RuntimeError("materialization failure has no exact Version")
            if attempt.status != "MATERIALIZING":
                return self._record(attempt)
            attempt.error_code = error_code
            attempt.error_message = error_message
            attempt.recovery_kind = "MATERIALIZATION"
            if attempt.materialization_retry_count >= max_auto_retries:
                attempt.status = "MATERIALIZATION_FAILED"
                attempt.recovery_state = "AVAILABLE"
            else:
                attempt.materialization_retry_count += 1
                attempt.recovery_state = "AUTO_RETRYING"
            session.flush()
            return self._record(attempt)

    def restart_recovery(
        self,
        *,
        space_id: int,
        skill_id: int,
        attempt_id: int,
        actor_id: str,
        env: str,
    ) -> PublicationRetryResult:
        with self._db.transactional_orm_session() as session:
            attempt = self._attempt(session, attempt_id=attempt_id, env=env, lock=True)
            self._require_attempt_scope(
                session,
                attempt=attempt,
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                env=env,
            )
            if attempt.status == "SUCCEEDED":
                return PublicationRetryResult(
                    attempt=self._record(attempt), task_required=False
                )
            if attempt.status == "FAILED":
                raise PublicationRequiresNewAttemptError(
                    "failed publication requires a new attempt"
                )
            if attempt.recovery_state == "AUTO_RETRYING":
                return PublicationRetryResult(
                    attempt=self._record(attempt), task_required=True
                )
            if attempt.recovery_state != "AVAILABLE":
                raise PublicationRecoveryNotAvailableError(
                    "publication recovery is not available"
                )
            if attempt.recovery_kind is None:
                raise RuntimeError("AVAILABLE recovery has no kind")
            attempt.recovery_state = "AUTO_RETRYING"
            if attempt.recovery_kind == "MATERIALIZATION":
                if attempt.status not in ("MATERIALIZING", "MATERIALIZATION_FAILED"):
                    raise RuntimeError("materialization recovery has invalid Attempt status")
                attempt.status = "MATERIALIZING"
                attempt.materialization_retry_count = 0
            attempt.error_code = None
            attempt.error_message = None
            session.flush()
            return PublicationRetryResult(
                attempt=self._record(attempt), task_required=True
            )
