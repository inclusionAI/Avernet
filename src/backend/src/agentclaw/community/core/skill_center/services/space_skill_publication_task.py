"""Durable worker for one-shot SC Publication and exact materialization."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Callable

from agentclaw.community.core.skill_center.skill_center_publication_gateway_protocol import (
    SkillCenterPublicationGatewayProtocol,
)
from agentclaw.community.core.events.bus import get_event_bus
from agentclaw.community.core.repository.protocols.space_skill_publication import (
    SpaceSkillPublicationRepositoryProtocol,
)
from agentclaw.community.core.skill_center.draft_content import (
    DraftContentStore,
    DraftRevisionRef,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    SkillVersionMaterializationError,
    SkillVersionMaterializationRequest,
    SkillVersionMaterializerProtocol,
)
from agentclaw.community.core.skill_center.publication_contract import (
    PublicationAttemptStatus,
    PublicationPackageStage,
    PublicationPackageStagerProtocol,
    PublicationRecoveryKind,
    PublicationRecoveryState,
    PublicationWork,
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
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterGatewayError,
    SkillCenterGatewayErrorCode,
    SkillCenterPublishState,
    SkillCenterPublishStatusRequest,
    SkillCenterPublishSubmitRequest,
    SkillCenterReadScope,
    SkillCenterTeamSkillDetailRequest,
    SkillCenterVersionListRequest,
    SkillCenterVisibility,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


SPACE_SKILL_PUBLICATION_TASK = "skill_center.publication"
SPACE_SKILL_PUBLICATION_DEADLINE_SECONDS = 30 * 60
SPACE_SKILL_PUBLICATION_AUTO_RETRY_SECONDS = 15 * 60
SPACE_SKILL_PUBLICATION_POLL_SECONDS = 2
_PACKAGE_URL_EXPIRES_SECONDS = 60 * 60
_SAFE_STORAGE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
logger = logging.getLogger(__name__)


def _safe_segment(value: str, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or value != value.strip()
        or _SAFE_STORAGE_SEGMENT.fullmatch(value) is None
    ):
        raise ValueError(f"{field} must be a safe non-empty path segment")
    return value


def publication_task_key(attempt_id: int, *, tenant: str = "teamclaw") -> str:
    if attempt_id < 1:
        raise ValueError("attempt_id must be positive")
    _safe_segment(tenant, field="tenant")
    return f"skill-publication:{tenant}:{attempt_id}"


def publication_task_payload(
    attempt_id: int, *, tenant: str = "teamclaw"
) -> dict[str, int | str]:
    publication_task_key(attempt_id, tenant=tenant)
    return {"attempt_id": attempt_id, "avernet_tenant": tenant}


def enqueue_publication_task(
    queue: TaskQueueService,
    *,
    attempt_id: int,
    tenant: str,
    deadline_seconds: int = SPACE_SKILL_PUBLICATION_DEADLINE_SECONDS,
) -> EnqueueResult:
    return queue.enqueue(
        SPACE_SKILL_PUBLICATION_TASK,
        publication_task_payload(attempt_id, tenant=tenant),
        deadline_seconds=deadline_seconds,
        idempotency_key=publication_task_key(attempt_id, tenant=tenant),
    )


class ObjectStoragePublicationPackageStager(PublicationPackageStagerProtocol):
    """Stage one attempt-scoped canonical ZIP and return a temporary GET URL."""

    def __init__(self, objects: ObjectStoragePlugin) -> None:
        self._objects = objects

    def stage(
        self,
        *,
        attempt_id: int,
        tenant: str,
        env: str,
        package,
    ) -> PublicationPackageStage:
        tenant = _safe_segment(tenant, field="tenant")
        env = _safe_segment(env, field="env")
        digest = hashlib.sha256(package.canonical_zip).hexdigest()
        key = (
            f"aidesktop/aidesktop_{env}/bolt_shared/skills-upload/"
            f"space-publications/{tenant}/{env}/{attempt_id}/{digest}.zip"
        )
        if not self._objects.put_object(key, package.canonical_zip):
            raise RuntimeError("Publication package staging failed")
        package_url = self._objects.sign_url(key, expires=_PACKAGE_URL_EXPIRES_SECONDS)
        if not isinstance(package_url, str) or not package_url.strip():
            raise RuntimeError("Publication package URL signing failed")
        return PublicationPackageStage(package_url=package_url)


class SpaceSkillPublicationTaskHandler:
    def __init__(
        self,
        *,
        repository: SpaceSkillPublicationRepositoryProtocol,
        gateway: SkillCenterPublicationGatewayProtocol,
        draft_store: DraftContentStore,
        stager: PublicationPackageStagerProtocol,
        materializer: SkillVersionMaterializerProtocol,
        tenant_provider: Callable[[], str],
        env_provider: Callable[[], str],
        auto_retry_seconds: int = SPACE_SKILL_PUBLICATION_AUTO_RETRY_SECONDS,
        poll_seconds: int = SPACE_SKILL_PUBLICATION_POLL_SECONDS,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._draft_store = draft_store
        self._stager = stager
        self._materializer = materializer
        self._tenant_provider = tenant_provider
        self._env_provider = env_provider
        self._auto_retry_seconds = auto_retry_seconds
        self._poll_seconds = poll_seconds

    @property
    def task_type(self) -> str:
        return SPACE_SKILL_PUBLICATION_TASK

    def handle(self, payload: dict | None) -> TaskOutcome:
        try:
            attempt_id, tenant = self._attempt_identity(payload)
        except ValueError as exc:
            return Fail(f"invalid Publication task payload: {exc}")
        with avernet_tenant_scope(tenant):
            return self._handle_attempt(attempt_id)

    def _handle_attempt(self, attempt_id: int) -> TaskOutcome:
        env = self._env_provider()
        work = self._repository.get_work(attempt_id=attempt_id, env=env)
        if work.attempt.recovery.state is PublicationRecoveryState.AVAILABLE:
            return Fail("Publication requires explicit Attempt retry")
        if work.attempt.status is PublicationAttemptStatus.PREPARING:
            prepared = self._prepare(work, env=env)
            if not isinstance(prepared, PublicationWork):
                return prepared
            claim = self._repository.claim_sc_submission(attempt_id=attempt_id, env=env)
            work = claim.work
            if claim.may_submit:
                submitted = self._submit(work, env=env)
                if submitted is not None:
                    return submitted
                return Reschedule(self._poll_seconds)
        if work.attempt.status in (
            PublicationAttemptStatus.SC_SUBMITTING,
            PublicationAttemptStatus.WAITING_SC,
            PublicationAttemptStatus.RESULT_UNKNOWN,
        ):
            return self._check_status(work, env=env)
        if work.attempt.status is PublicationAttemptStatus.MATERIALIZING:
            return self._materialize(work, env=env)
        if work.attempt.status is PublicationAttemptStatus.SUCCEEDED:
            # Crash closure: a task may be reclaimed after DB success but before
            # the unified Published seam was delivered. Re-materialization is an
            # idempotent verify of the same exact Version; event delivery is
            # consequently at-least-once.
            return self._materialize(work, env=env, already_succeeded=True)
        return Complete()

    def _prepare(
        self, work: PublicationWork, *, env: str
    ) -> PublicationWork | TaskOutcome:
        frozen_locator = work.attempt.frozen_draft_locator
        if not work.skill_uuid or not frozen_locator or not work.sc_team_id:
            return self._retry_or_available(
                work,
                kind=PublicationRecoveryKind.PREPARATION,
                error_code="PUBLICATION_PREPARATION_FAILED",
                error_message="Publication prerequisites are incomplete",
                env=env,
            )
        stage = "draft_read"
        try:
            ref = DraftRevisionRef.from_locator(
                tenant=self._tenant_provider(), env=env, locator=frozen_locator
            )
            package = self._draft_store.read_revision(ref)
            if package.name != work.skill_name:
                self._repository.mark_failed(
                    attempt_id=work.attempt.attempt_id,
                    error_code="SKILL_NAME_CHANGED",
                    error_message="Frozen Draft name differs from the Skill identity",
                    env=env,
                )
                return Complete()
            stage = "package_stage"
            package_stage = self._stager.stage(
                attempt_id=work.attempt.attempt_id,
                tenant=self._tenant_provider(),
                env=env,
                package=package,
            )
            return self._repository.mark_prepared(
                attempt_id=work.attempt.attempt_id,
                package_url=package_stage.package_url,
                env=env,
            )
        except Exception as exc:
            self._log_failure(
                operation="publication_prepare",
                stage=stage,
                work=work,
                env=env,
                failure_type=type(exc).__name__,
            )
            return self._retry_or_available(
                work,
                kind=PublicationRecoveryKind.PREPARATION,
                error_code="PUBLICATION_PREPARATION_FAILED",
                error_message="Publication preparation failed",
                env=env,
            )

    def _submit(self, work: PublicationWork, *, env: str) -> TaskOutcome | None:
        assert work.sc_team_id is not None
        assert work.package_url is not None
        try:
            self._gateway.submit_publish(
                SkillCenterPublishSubmitRequest(
                    team_id=work.sc_team_id,
                    skill_code=work.skill_uuid,
                    skill_name=work.skill_name,
                    version_number=str(work.attempt.sc_version_number),
                    package_url=work.package_url,
                    description=work.draft_description,
                    visibility=SkillCenterVisibility.PRIVATE,
                    creator_work_no=work.attempt.created_by,
                )
            )
        except SkillCenterGatewayError as exc:
            self._log_failure(
                operation="publication_submit",
                stage="publish_submit",
                work=work,
                env=env,
                failure_type=type(exc).__name__,
                gateway_error=exc,
            )
            if exc.code in (
                SkillCenterGatewayErrorCode.BUSINESS,
                SkillCenterGatewayErrorCode.TEAM_NOT_FOUND,
            ):
                self._repository.mark_failed(
                    attempt_id=work.attempt.attempt_id,
                    error_code="SC_PUBLISH_REJECTED",
                    error_message="Skill Center rejected publication",
                    env=env,
                )
                return Complete()
            return self._unknown_or_available(work, env=env)
        self._repository.mark_waiting_sc(
            attempt_id=work.attempt.attempt_id, env=env
        )
        return None

    def _check_status(self, work: PublicationWork, *, env: str) -> TaskOutcome:
        try:
            status = self._gateway.get_publish_status(
                SkillCenterPublishStatusRequest(work.skill_uuid)
            )
            if status.version_number != work.attempt.sc_version_number:
                raise SkillCenterGatewayError(
                    SkillCenterGatewayErrorCode.UNKNOWN_RESPONSE,
                    "SC status refers to another version",
                )
        except SkillCenterGatewayError as exc:
            self._log_failure(
                operation="publication_status",
                stage="publish_status",
                work=work,
                env=env,
                failure_type=type(exc).__name__,
                gateway_error=exc,
            )
            return self._unknown_or_available(work, env=env)
        if status.status is SkillCenterPublishState.PENDING:
            if self._expired(work):
                self._repository.mark_recovery_available(
                    attempt_id=work.attempt.attempt_id,
                    kind=PublicationRecoveryKind.SC_STATUS_CHECK,
                    error_code="SC_MARKET_UNAVAILABLE",
                    error_message="Skill Center publication did not settle before the automatic retry horizon",
                    env=env,
                )
                return Fail("Skill Center publication requires explicit recovery")
            return Reschedule(self._poll_seconds)
        if status.status is SkillCenterPublishState.FAILED:
            self._repository.mark_failed(
                attempt_id=work.attempt.attempt_id,
                error_code="SC_PUBLISH_REJECTED",
                error_message="Skill Center rejected publication",
                env=env,
            )
            return Complete()

        # The publish result is now known even if SC's Team/version read models
        # have not converged yet. Clear RESULT_UNKNOWN before exact discovery so
        # eventual-consistency lag stays WAITING_SC rather than being presented
        # as an uncertain external publish outcome.
        self._repository.mark_waiting_sc(
            attempt_id=work.attempt.attempt_id, env=env
        )
        work = self._repository.get_work(attempt_id=work.attempt.attempt_id, env=env)
        try:
            assert work.sc_team_id is not None
            team_skill = self._gateway.get_team_skill(
                SkillCenterTeamSkillDetailRequest(
                    team_id=work.sc_team_id, skill_code=work.skill_uuid
                )
            )
            if team_skill is None:
                raise SkillCenterGatewayError(
                    SkillCenterGatewayErrorCode.UNKNOWN_RESPONSE,
                    "SC published status has no Team Skill",
                )
            versions = self._gateway.list_versions(
                SkillCenterVersionListRequest(
                    skill_code=work.skill_uuid,
                    scope=SkillCenterReadScope.TEAM,
                    team_id=work.sc_team_id,
                )
            )
            exact = next(
                (
                    version
                    for version in versions
                    if version.version_number == work.attempt.sc_version_number
                ),
                None,
            )
            if exact is None:
                raise SkillCenterGatewayError(
                    SkillCenterGatewayErrorCode.UNKNOWN_RESPONSE,
                    "SC published exact Version is not listable yet",
                )
            try:
                sc_skill_id = int(team_skill.skill_id or "")
                sc_version_id = int(exact.version_id or "")
            except ValueError as exc:
                raise SkillCenterGatewayError(
                    SkillCenterGatewayErrorCode.PROTOCOL,
                    "SC exact Version omitted numeric identity",
                ) from exc
            work = self._repository.begin_materialization(
                attempt_id=work.attempt.attempt_id,
                sc_skill_id=sc_skill_id,
                sc_version_id=sc_version_id,
                sc_sha256=exact.sha256,
                env=env,
            )
            return self._materialize(work, env=env)
        except SkillCenterGatewayError as exc:
            self._log_failure(
                operation="publication_status_or_version_discovery",
                stage="status_or_version_discovery",
                work=work,
                env=env,
                failure_type=type(exc).__name__,
                gateway_error=exc,
            )
            return self._retry_or_available(
                work,
                kind=PublicationRecoveryKind.SC_STATUS_CHECK,
                error_code="SC_MARKET_UNAVAILABLE",
                error_message="Published Skill Center Version metadata is not ready",
                env=env,
            )

    def _materialize(
        self,
        work: PublicationWork,
        *,
        env: str,
        already_succeeded: bool = False,
    ) -> TaskOutcome:
        if work.attempt.skill_version_id is None or work.sc_team_id is None:
            return Fail("Publication materialization has incomplete identity")
        try:
            published = self._materializer.materialize(
                SkillVersionMaterializationRequest(
                    env=env,
                    skill_id=work.attempt.skill_id,
                    skill_version_id=work.attempt.skill_version_id,
                    scope=SkillCenterReadScope.TEAM,
                    team_id=work.sc_team_id,
                )
            )
            if not already_succeeded:
                self._repository.complete_success(
                    attempt_id=work.attempt.attempt_id,
                    skill_version_id=published.skill_version_id,
                    env=env,
                )
                self._delete_frozen_draft_best_effort(work, env=env)
            get_event_bus().publish(published)
            return Complete()
        except SkillVersionMaterializationError as exc:
            self._log_failure(
                operation="publication_materialization",
                stage=exc.stage or "unknown",
                work=work,
                env=env,
                failure_type=type(exc.__cause__ or exc).__name__,
            )
            return self._retry_or_available(
                work,
                kind=PublicationRecoveryKind.MATERIALIZATION,
                error_code="MATERIALIZATION_FAILED",
                error_message="Exact Version materialization failed",
                env=env,
            )

    def _delete_frozen_draft_best_effort(
        self, work: PublicationWork, *, env: str
    ) -> None:
        frozen_locator = work.attempt.frozen_draft_locator
        if not frozen_locator:
            return
        try:
            self._draft_store.delete_revision(
                DraftRevisionRef.from_locator(
                    tenant=self._tenant_provider(),
                    env=env,
                    locator=frozen_locator,
                )
            )
        except Exception:
            logger.exception(
                "failed to clean published Space Skill Draft revision",
                extra={"attempt_id": work.attempt.attempt_id},
            )

    @staticmethod
    def _log_failure(
        *,
        operation: str,
        stage: str,
        work: PublicationWork,
        env: str,
        failure_type: str,
        gateway_error: SkillCenterGatewayError | None = None,
    ) -> None:
        """Keep retry semantics unchanged while retaining safe upstream correlation facts."""
        diagnostics = {
            "operation": operation,
            "stage": stage,
            "env": env,
            "attempt_id": work.attempt.attempt_id,
            "space_id": work.space_id,
            "skill_id": work.attempt.skill_id,
            "skill_uuid": work.skill_uuid,
            "team_id": work.sc_team_id,
            "sc_version_number": work.attempt.sc_version_number,
            "skill_version_id": work.attempt.skill_version_id,
            "gateway_error_code": (
                gateway_error.code.value if gateway_error is not None else None
            ),
            "upstream_code": (
                gateway_error.upstream_code if gateway_error is not None else None
            ),
            "upstream_trace_id": (
                gateway_error.trace_id if gateway_error is not None else None
            ),
            "failure_type": failure_type,
        }
        logger.warning(
            "skill_center_publication_failed "
            "operation=%(operation)s stage=%(stage)s env=%(env)s "
            "attempt_id=%(attempt_id)s space_id=%(space_id)s "
            "skill_id=%(skill_id)s skill_uuid=%(skill_uuid)s team_id=%(team_id)s "
            "sc_version_number=%(sc_version_number)s skill_version_id=%(skill_version_id)s "
            "gateway_error_code=%(gateway_error_code)s upstream_code=%(upstream_code)s "
            "upstream_trace_id=%(upstream_trace_id)s failure_type=%(failure_type)s",
            diagnostics,
            extra=diagnostics,
        )

    def _unknown_or_available(self, work: PublicationWork, *, env: str) -> TaskOutcome:
        available = self._expired(work)
        self._repository.mark_result_unknown(
            attempt_id=work.attempt.attempt_id,
            error_code="SC_MARKET_UNAVAILABLE",
            error_message="Skill Center publication status is temporarily unavailable",
            recovery_available=available,
            env=env,
        )
        safe_error = "Skill Center publication status is temporarily unavailable"
        return Fail(safe_error) if available else Retry(safe_error)

    def _retry_or_available(
        self,
        work: PublicationWork,
        *,
        kind: PublicationRecoveryKind,
        error_code: str,
        error_message: str,
        env: str,
    ) -> TaskOutcome:
        if self._expired(work):
            self._repository.mark_recovery_available(
                attempt_id=work.attempt.attempt_id,
                kind=kind,
                error_code=error_code,
                error_message=error_message,
                env=env,
            )
            return Fail(error_message)
        return Retry(error_message)

    def _expired(self, work: PublicationWork) -> bool:
        start = (
            work.attempt.sc_accepted_at
            if work.attempt.status is PublicationAttemptStatus.MATERIALIZING
            else work.attempt.sc_post_started_at
            if work.attempt.status
            in (
                PublicationAttemptStatus.SC_SUBMITTING,
                PublicationAttemptStatus.WAITING_SC,
                PublicationAttemptStatus.RESULT_UNKNOWN,
            )
            else work.attempt.gmt_created
        )
        start = start or work.attempt.gmt_modified or work.attempt.gmt_created
        now = work.database_now
        if start.tzinfo is None and now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        elif start.tzinfo is not None and now.tzinfo is None:
            start = start.replace(tzinfo=None)
        return (now - start).total_seconds() >= self._auto_retry_seconds

    @staticmethod
    def _attempt_identity(payload: dict | None) -> tuple[int, str]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        attempt_id = payload.get("attempt_id")
        if type(attempt_id) is not int or attempt_id < 1:
            raise ValueError("attempt_id must be a positive integer")
        tenant = payload.get("avernet_tenant")
        _safe_segment(tenant, field="avernet_tenant")
        return attempt_id, tenant


class SpaceSkillPublicationTaskLifecycle(LifecycleBase):
    def __init__(
        self, *, registry: HandlerRegistry, handler: SpaceSkillPublicationTaskHandler
    ) -> None:
        self._registry = registry
        self._handler = handler

    async def bootstrap(self) -> None:
        self._registry.register(self._handler, wake_on_enqueue=True)


__all__ = [
    "ObjectStoragePublicationPackageStager",
    "SPACE_SKILL_PUBLICATION_AUTO_RETRY_SECONDS",
    "SPACE_SKILL_PUBLICATION_DEADLINE_SECONDS",
    "SPACE_SKILL_PUBLICATION_TASK",
    "SpaceSkillPublicationTaskHandler",
    "SpaceSkillPublicationTaskLifecycle",
    "enqueue_publication_task",
    "publication_task_key",
    "publication_task_payload",
]
