"""Durable, independent scan and metadata-sync tasks for Local Skill uploads."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import logging
from typing import Callable

from agentclaw.community.core.repository.protocols.local_skill_security_observation import (
    LocalSkillSecurityObservationRepositoryProtocol,
)
from agentclaw.community.core.skill_center.local_skill_security_observation import (
    LocalSkillSecurityIdentity,
    LocalSkillSecurityObservation,
)
from agentclaw.community.plugin_api.local_skill_security_provider import (
    LocalSkillSecurityProviderProtocol,
    SecurityArtifactAccessRequest,
    SecurityCheckReport,
    SecurityRemoteArtifact,
    SecuritySkillInfo,
    SecuritySubmissionRejected,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.types import (
    Complete,
    EnqueueResult,
    Fail,
    Reschedule,
    Retry,
    TaskOutcome,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.plugin_api.object_storage import (
    ImmutableObjectStorageCapability,
    ObjectReadStatus,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


SCAN_TASK = "local_skill_security_scan"
SYNC_TASK = "local_skill_security_sync"
TASK_DEADLINE_SECONDS = 30 * 60
POLL_SECONDS = 10
LOCAL_SKILL_VERSION = "1.0"
logger = logging.getLogger(__name__)


def security_task_payload(
    *, tenant: str, env: str, observation_id: str
) -> dict[str, str]:
    if not all(
        isinstance(value, str) and value.strip() == value and value
        for value in (tenant, env, observation_id)
    ):
        raise ValueError("tenant, env and observation_id must be non-empty")
    return {"avernet_tenant": tenant, "env": env, "observation_id": observation_id}


def enqueue_security_scan_task(
    queue: TaskQueueService, *, tenant: str, env: str, observation_id: str
) -> EnqueueResult:
    return queue.enqueue(
        SCAN_TASK,
        security_task_payload(tenant=tenant, env=env, observation_id=observation_id),
        deadline_seconds=TASK_DEADLINE_SECONDS,
        idempotency_key=f"local-security-scan:{observation_id}",
    )


def enqueue_security_sync_task(
    queue: TaskQueueService,
    *,
    tenant: str,
    env: str,
    observation_id: str,
    provider_skill_id: str,
) -> EnqueueResult:
    if not provider_skill_id:
        raise ValueError("provider_skill_id is required")
    return queue.enqueue(
        SYNC_TASK,
        security_task_payload(tenant=tenant, env=env, observation_id=observation_id),
        deadline_seconds=TASK_DEADLINE_SECONDS,
        idempotency_key=f"local-security-sync:{provider_skill_id}",
    )


def _task_identity(
    payload: dict | None, env_provider: Callable[[], str]
) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    tenant, env, observation_id = (
        payload.get(key) for key in ("avernet_tenant", "env", "observation_id")
    )
    security_task_payload(tenant=tenant, env=env, observation_id=observation_id)
    if env != env_provider():
        raise ValueError("payload env differs from this worker")
    return tenant, env, observation_id


def _skill_info(
    *,
    tenant: str,
    env: str,
    observation: LocalSkillSecurityObservation,
    identity: LocalSkillSecurityIdentity,
) -> SecuritySkillInfo:
    if not observation.asset_deleted and observation.external_artifact is None:
        raise ValueError("external artifact has not been staged")
    return SecuritySkillInfo(
        tenant=tenant,
        env=env,
        provider_skill_id=observation.provider_skill_id,
        asset_skill_id=observation.skill_id,
        observation_id=observation.observation_id,
        snapshot_ref=observation.snapshot_ref,
        upload_order=observation.upload_order,
        name=observation.name,
        description=observation.description,
        version=LOCAL_SKILL_VERSION,
        owner_id=observation.owner_id,
        creator_id=identity.creator_id,
        modifier_id=observation.modifier_id,
        status="OFFLINE" if observation.asset_deleted else "ONLINE",
        platform_del_flag=1 if observation.asset_deleted else 0,
        artifact=(
            SecurityRemoteArtifact(**observation.external_artifact)
            if observation.external_artifact is not None else None
        ),
    )


class _BaseSecurityTaskHandler:
    def __init__(
        self,
        *,
        repository: LocalSkillSecurityObservationRepositoryProtocol,
        provider: LocalSkillSecurityProviderProtocol,
        env_provider: Callable[[], str],
        enabled: bool,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._env_provider = env_provider
        self._enabled = enabled

    def _load(
        self, env: str, observation_id: str, *, allow_deleted_poll: bool = False
    ) -> tuple[LocalSkillSecurityObservation, LocalSkillSecurityIdentity]:
        observation = self._repository.get_by_observation_id(
            env=env, observation_id=observation_id
        )
        if observation is None or observation.skill_id is None:
            raise LookupError("security observation or formal asset is missing")
        if not (allow_deleted_poll and observation.check_id is not None) and (
            self._repository.get_observed_incarnation_id(
                env=env,
                bot_id=observation.bot_id,
                owner_id=observation.owner_id,
                skill_id=observation.skill_id,
            )
            != observation.incarnation_id
        ):
            raise LookupError("current Local Skill security identity has changed")
        identity = self._repository.get_identity_for_asset(
            env=env,
            bot_id=observation.bot_id,
            owner_id=observation.owner_id,
            skill_id=observation.skill_id,
            incarnation_id=observation.incarnation_id,
        )
        if (
            identity is None
            or identity.provider_skill_id != observation.provider_skill_id
        ):
            raise LookupError("security identity binding is missing")
        return observation, identity


class LocalSkillSecurityScanTaskHandler(_BaseSecurityTaskHandler):
    def __init__(
        self,
        *,
        repository: LocalSkillSecurityObservationRepositoryProtocol,
        snapshots: ImmutableObjectStorageCapability | None,
        provider: LocalSkillSecurityProviderProtocol,
        env_provider: Callable[[], str],
        poll_seconds: int = POLL_SECONDS,
        enabled: bool,
    ) -> None:
        if enabled and snapshots is None:
            raise ValueError(
                "enabled security scan requires immutable snapshot storage"
            )
        super().__init__(
            repository=repository,
            provider=provider,
            env_provider=env_provider,
            enabled=enabled,
        )
        self._snapshots = snapshots
        self._poll_seconds = poll_seconds

    @property
    def task_type(self) -> str:
        return SCAN_TASK

    def handle(self, payload: dict | None) -> TaskOutcome:
        if not self._enabled:
            # Leave the observation untouched. On re-enable the reconciler
            # recreates scan work from its durable pending state.
            return Complete()
        try:
            tenant, env, observation_id = _task_identity(payload, self._env_provider)
        except ValueError as exc:
            return Fail(f"invalid security scan task: {exc}")
        with avernet_tenant_scope(tenant):
            return self._handle_observation(tenant, env, observation_id)

    def _handle_observation(
        self, tenant: str, env: str, observation_id: str
    ) -> TaskOutcome:
        try:
            # A submitted check is tied to an immutable snapshot. Deleting the
            # formal asset must not strand its historical report; the identity
            # lookup below still requires the same incarnation and provider ID.
            observation, identity = self._load(
                env, observation_id, allow_deleted_poll=True
            )
        except LookupError as exc:
            return Fail(str(exc))
        if observation.processing_status == "COMPLETED":
            return Complete()
        if observation.processing_status == "SUBMIT_UNKNOWN":
            return Fail(
                "scan submission outcome is unknown; provider reconciliation required"
            )
        if observation.processing_status == "FAILED" and observation.error_code not in (
            "SNAPSHOT_UNAVAILABLE",
            "ARTIFACT_EXPOSE_FAILED",
            "REPORT_POLL_FAILED",
            "TASK_ENQUEUE_FAILED",
        ):
            return Fail("scan failure requires explicit recovery")
        if observation.check_id is not None:
            return self._poll(tenant, env, observation)
        if observation.processing_status == "SUBMITTING":
            self._repository.mark_submit_unknown(
                env=env,
                observation_id=observation_id,
                error_code="SUBMIT_RESPONSE_UNKNOWN",
                error_message="A prior submission may have succeeded without a recorded check ID",
            )
            return Fail(
                "scan submission outcome is unknown; provider reconciliation required"
            )
        if observation.external_artifact is None:
            staged = self._stage(tenant, env, observation)
            if not isinstance(staged, LocalSkillSecurityObservation):
                return staged
            observation = staged
        info = _skill_info(
            tenant=tenant, env=env, observation=observation, identity=identity
        )
        # An atomic persistence claim is the last local operation before the
        # external POST. A crash after this point is ambiguous, never a retry.
        if not self._repository.claim_scan_submission(
            env=env, observation_id=observation_id
        ):
            return Reschedule(self._poll_seconds)
        try:
            report = self._provider.submit_scan(info)
        except SecuritySubmissionRejected:
            self._repository.mark_scan_error(
                env=env,
                observation_id=observation_id,
                error_code="PROVIDER_SUBMISSION_REJECTED",
                error_message="Security provider rejected scan submission",
            )
            return Fail("security provider rejected scan submission")
        except Exception as exc:
            logger.warning(
                "Local Skill scan submission is uncertain: %s", type(exc).__name__
            )
            self._repository.mark_submit_unknown(
                env=env,
                observation_id=observation_id,
                error_code="SUBMIT_RESPONSE_UNKNOWN",
                error_message="Scan submission response was not durably recorded",
            )
            return Fail(
                "scan submission outcome is unknown; provider reconciliation required"
            )
        if not report.check_id:
            self._repository.mark_submit_unknown(
                env=env,
                observation_id=observation_id,
                error_code="CHECK_ID_MISSING",
                error_message="Provider response did not contain a check ID",
            )
            return Fail("provider response did not contain a check ID")
        self._repository.save_check_submission(
            env=env, observation_id=observation_id, check_id=report.check_id
        )
        return self._report_outcome(env, observation_id, report)

    def _stage(
        self, tenant: str, env: str, observation: LocalSkillSecurityObservation
    ) -> LocalSkillSecurityObservation | TaskOutcome:
        assert self._snapshots is not None
        try:
            read = self._snapshots.read_object(observation.snapshot_ref)
        except Exception as exc:
            logger.warning("Local Skill snapshot read failed: %s", type(exc).__name__)
            self._repository.mark_scan_error(
                env=env,
                observation_id=observation.observation_id,
                error_code="SNAPSHOT_UNAVAILABLE",
                error_message="Snapshot storage read failed",
            )
            return Retry("snapshot storage unavailable")
        if read.status is ObjectReadStatus.FAILED:
            self._repository.mark_scan_error(
                env=env,
                observation_id=observation.observation_id,
                error_code="SNAPSHOT_UNAVAILABLE",
                error_message="Snapshot storage read failed",
            )
            return Retry("snapshot storage unavailable")
        if read.status is not ObjectReadStatus.FOUND or read.content is None:
            self._repository.mark_scan_error(
                env=env,
                observation_id=observation.observation_id,
                error_code="SNAPSHOT_MISSING",
                error_message="Immutable upload snapshot is missing",
            )
            return Fail("immutable upload snapshot is missing")
        digest = hashlib.sha256(read.content).hexdigest()
        expected = observation.sha256.removeprefix("sha256:")
        if digest != expected:
            self._repository.mark_scan_error(
                env=env,
                observation_id=observation.observation_id,
                error_code="SNAPSHOT_DIGEST_MISMATCH",
                error_message="Immutable upload snapshot digest differs",
            )
            return Fail("immutable upload snapshot digest differs")
        try:
            artifact = self._provider.expose_artifact(
                SecurityArtifactAccessRequest(
                    tenant=tenant,
                    env=env,
                    provider_skill_id=observation.provider_skill_id,
                    observation_id=observation.observation_id,
                    version=LOCAL_SKILL_VERSION,
                    sha256=digest,
                    snapshot_ref=observation.snapshot_ref,
                )
            )
        except Exception as exc:
            logger.warning(
                "Local Skill artifact exposure failed: %s", type(exc).__name__
            )
            self._repository.mark_scan_error(
                env=env,
                observation_id=observation.observation_id,
                error_code="ARTIFACT_EXPOSE_FAILED",
                error_message="Security artifact exposure failed",
            )
            return Retry("security artifact exposure failed")
        return self._repository.save_external_artifact(
            env=env,
            observation_id=observation.observation_id,
            artifact=asdict(artifact),
        )

    def _poll(
        self, tenant: str, env: str, observation: LocalSkillSecurityObservation
    ) -> TaskOutcome:
        assert observation.check_id is not None
        try:
            report = self._provider.get_scan_report(
                tenant=tenant, env=env, check_id=observation.check_id
            )
        except Exception as exc:
            logger.warning("Local Skill report poll failed: %s", type(exc).__name__)
            self._repository.mark_scan_error(
                env=env,
                observation_id=observation.observation_id,
                error_code="REPORT_POLL_FAILED",
                error_message="Security report poll failed",
            )
            return Retry("security report poll failed")
        if report.check_id != observation.check_id:
            self._repository.mark_scan_error(
                env=env,
                observation_id=observation.observation_id,
                error_code="REPORT_CHECK_ID_MISMATCH",
                error_message="Report refers to a different check ID",
            )
            return Fail("report refers to a different check ID")
        return self._report_outcome(env, observation.observation_id, report)

    def _report_outcome(
        self, env: str, observation_id: str, report: SecurityCheckReport
    ) -> TaskOutcome:
        if report.risk_status == "DETECTING":
            self._repository.save_in_progress_report(
                env=env,
                observation_id=observation_id,
                check_id=report.check_id,
                risk_status=report.risk_status,
                report=report.raw_report,
                detail_url=report.detail_url,
            )
            return Reschedule(self._poll_seconds)
        if report.risk_status not in ("PASS", "REJECT"):
            self._repository.mark_scan_error(
                env=env,
                observation_id=observation_id,
                error_code="UNSUPPORTED_RISK_STATUS",
                error_message="Provider returned an unknown risk status",
            )
            return Fail("provider returned an unknown risk status")
        self._repository.save_report(
            env=env,
            observation_id=observation_id,
            check_id=report.check_id,
            risk_status=report.risk_status,
            report=report.raw_report,
            detail_url=report.detail_url,
        )
        return Complete()


class LocalSkillSecuritySyncTaskHandler(_BaseSecurityTaskHandler):
    def __init__(
        self,
        *,
        repository: LocalSkillSecurityObservationRepositoryProtocol,
        provider: LocalSkillSecurityProviderProtocol,
        env_provider: Callable[[], str],
        poll_seconds: int = POLL_SECONDS,
        enabled: bool,
    ) -> None:
        super().__init__(
            repository=repository,
            provider=provider,
            env_provider=env_provider,
            enabled=enabled,
        )
        self._poll_seconds = poll_seconds

    @property
    def task_type(self) -> str:
        return SYNC_TASK

    def handle(self, payload: dict | None) -> TaskOutcome:
        if not self._enabled:
            # The current sync target remains pending for the reconciler.
            return Complete()
        try:
            tenant, env, observation_id = _task_identity(payload, self._env_provider)
        except ValueError as exc:
            return Fail(f"invalid security sync task: {exc}")
        with avernet_tenant_scope(tenant):
            original = self._repository.get_by_observation_id(
                env=env, observation_id=observation_id
            )
            if original is None or original.skill_id is None:
                return Fail("security sync source is missing")
            identity = self._repository.get_identity_for_asset(
                env=env, bot_id=original.bot_id, owner_id=original.owner_id,
                skill_id=original.skill_id,
                incarnation_id=original.incarnation_id,
            )
            if identity is None or identity.provider_skill_id != original.provider_skill_id:
                return Fail("security identity binding is missing")
            target_id = identity.sync_target_observation_id
            if target_id is None or identity.sync_completed_observation_id == target_id:
                return Complete()
            observation = self._repository.get_by_observation_id(
                env=env, observation_id=target_id
            )
            if (
                observation is None
                or observation.provider_skill_id != original.provider_skill_id
            ):
                return Fail("sync target is missing or belongs to a different Skill")
            if observation.external_artifact is None and not observation.asset_deleted:
                if observation.processing_status in {"FAILED", "SUBMIT_UNKNOWN"} and (
                    observation.error_code
                    not in {
                        "SNAPSHOT_UNAVAILABLE",
                        "ARTIFACT_EXPOSE_FAILED",
                        "REPORT_POLL_FAILED",
                        "TASK_ENQUEUE_FAILED",
                    }
                ):
                    self._repository.mark_sync_error(
                        env=env,
                        bot_id=observation.bot_id,
                        owner_id=observation.owner_id,
                        skill_id=observation.skill_id,
                        incarnation_id=observation.incarnation_id,
                        observation_id=target_id,
                        error_code="ARTIFACT_UNAVAILABLE",
                        error_message="Scan ended without an available artifact",
                    )
                    return Fail("scan ended without an available artifact")
                return Reschedule(self._poll_seconds)
            info = _skill_info(
                tenant=tenant, env=env, observation=observation, identity=identity
            )
            try:
                self._provider.sync_skill_info(info)
            except Exception as exc:
                logger.warning(
                    "Local Skill metadata sync failed: %s", type(exc).__name__
                )
                self._repository.mark_sync_error(
                    env=env,
                    bot_id=observation.bot_id,
                    owner_id=observation.owner_id,
                    skill_id=observation.skill_id,
                    incarnation_id=observation.incarnation_id,
                    observation_id=target_id,
                    error_code="PROVIDER_SYNC_FAILED",
                    error_message="Security metadata sync failed",
                )
                return Retry("security metadata sync failed")
            current = self._repository.mark_sync_completed(
                env=env,
                bot_id=observation.bot_id,
                owner_id=observation.owner_id,
                skill_id=observation.skill_id,
                incarnation_id=observation.incarnation_id,
                observation_id=target_id,
            )
            return Complete() if current else Reschedule(self._poll_seconds)


class LocalSkillSecurityTaskLifecycle(LifecycleBase):
    def __init__(
        self,
        *,
        registry: HandlerRegistry,
        scan: LocalSkillSecurityScanTaskHandler,
        sync: LocalSkillSecuritySyncTaskHandler,
    ) -> None:
        self._registry = registry
        self._scan = scan
        self._sync = sync

    async def bootstrap(self) -> None:
        self._registry.register(self._scan, wake_on_enqueue=True)
        self._registry.register(self._sync, wake_on_enqueue=True)


__all__ = [
    "SCAN_TASK",
    "SYNC_TASK",
    "LocalSkillSecurityScanTaskHandler",
    "LocalSkillSecuritySyncTaskHandler",
    "LocalSkillSecurityTaskLifecycle",
    "enqueue_security_scan_task",
    "enqueue_security_sync_task",
    "security_task_payload",
]
