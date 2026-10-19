"""Persist the exact package and hand off independent Local Skill security work."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from threading import Event
from uuid import uuid4

from agentclaw.community.core.repository.protocols.local_skill_security_observation import (
    LocalSkillSecurityObservationRepositoryProtocol,
    ObservationHandoffCancelledError,
)
from agentclaw.community.core.skill_center.services.local_skill_security_tasks import (
    enqueue_security_scan_task,
    enqueue_security_sync_task,
)
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.plugin_api.object_storage import (
    ImmutableObjectStorageCapability,
    ObjectCreateResult,
    ObjectReadStatus,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

logger = logging.getLogger(__name__)


class NoopLocalSkillSecurityUploadHook:
    """Skip observation without reading or writing security identity."""

    def current_incarnation_id(
        self, *, bot_id: str, owner_id: str, skill_id: int
    ) -> str | None:
        return None

    def observe(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        skill_id: int,
        operation: str,
        name: str,
        description: str,
        canonical_zip: bytes,
        observation_id: str,
        incarnation_id: str,
        cancelled: Event,
    ) -> None:
        return None

    def record_handoff_timeout(self, **_kwargs: object) -> None:
        return None

    def record_deletion(self, **_kwargs: object) -> None:
        return None


class LocalSkillSecurityUploadObserver:
    """Record one successful upload while its edit guard preserves order.

    This service does no security-provider I/O. A caller that has already
    committed the formal asset may safely catch any exception it raises; the
    snapshot key and stage-specific error are logged for recovery.
    """

    def __init__(
        self,
        *,
        repository: LocalSkillSecurityObservationRepositoryProtocol,
        objects: ImmutableObjectStorageCapability,
        queue: TaskQueueService,
        env_provider: Callable[[], str],
    ) -> None:
        if not isinstance(objects, ImmutableObjectStorageCapability):
            raise ValueError("Local Skill security requires immutable object storage")
        self._repository = repository
        self._objects = objects
        self._queue = queue
        self._env_provider = env_provider

    def current_incarnation_id(
        self, *, bot_id: str, owner_id: str, skill_id: int
    ) -> str | None:
        env = self._env_provider()
        existing = self._repository.get_observed_incarnation_id(
            env=env, bot_id=bot_id, owner_id=owner_id, skill_id=skill_id,
        )
        if existing is not None:
            if not existing.strip():
                raise ValueError("observed Local Skill security identity is empty")
            return existing
        return str(uuid4())

    def record_deletion(
        self, *, bot_id: str, owner_id: str, actor_id: str, skill_id: int,
    ) -> None:
        tenant = get_current_avernet_tenant()
        env = self._env_provider()
        identity = self._repository.record_asset_deleted(
            env=env, bot_id=bot_id, owner_id=owner_id,
            skill_id=skill_id, actor_id=actor_id,
        )
        if identity is None:
            logger.warning(
                "Local Skill deletion has no recorded security identity "
                "tenant=%s env=%s bot_id=%s skill_id=%s",
                tenant, env, bot_id, skill_id,
            )
            return
        observation_id = identity.sync_target_observation_id
        assert observation_id is not None
        try:
            enqueue_security_sync_task(
                self._queue, tenant=tenant, env=env,
                observation_id=observation_id,
                provider_skill_id=identity.provider_skill_id,
            )
        except Exception:
            logger.exception(
                "Local Skill deletion sync enqueue failed tenant=%s env=%s "
                "bot_id=%s skill_id=%s observation_id=%s",
                tenant, env, bot_id, skill_id, observation_id,
            )
            self._repository.mark_sync_error(
                env=env, bot_id=bot_id, owner_id=owner_id,
                skill_id=skill_id, incarnation_id=identity.incarnation_id,
                observation_id=observation_id,
                error_code="TASK_ENQUEUE_FAILED",
                error_message="Deletion metadata sync task enqueue failed",
            )

    def observe(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        skill_id: int,
        operation: str,
        name: str,
        description: str,
        canonical_zip: bytes,
        observation_id: str,
        incarnation_id: str,
        cancelled: Event,
    ) -> None:
        tenant = get_current_avernet_tenant()
        env = self._env_provider()
        digest = "sha256:" + hashlib.sha256(canonical_zip).hexdigest()
        snapshot_ref = self._snapshot_ref(tenant, env, observation_id)
        if cancelled.is_set():
            return
        creator_id = (
            actor_id
            if operation == "created"
            else self._first_uploader(
                bot_id=bot_id,
                owner_id=owner_id,
                skill_id=skill_id,
                env=env,
                incarnation_id=incarnation_id,
            )
        )
        if cancelled.is_set():
            return
        try:
            upload_order = self._repository.reserve_handoff(
                env=env,
                bot_id=bot_id,
                owner_id=owner_id,
                actor_id=actor_id,
                skill_id=skill_id,
                observation_id=observation_id,
                sha256=digest,
                snapshot_ref=snapshot_ref,
                cancelled=cancelled,
                name=name,
                description=description,
                creator_id=creator_id,
                incarnation_id=incarnation_id,
            )
        except ObservationHandoffCancelledError:
            return
        except Exception:
            if cancelled.is_set():
                return
            self._record_failure(
                tenant=tenant,
                env=env,
                bot_id=bot_id,
                owner_id=owner_id,
                actor_id=actor_id,
                skill_id=skill_id,
                observation_id=observation_id,
                digest=digest,
                snapshot_ref=snapshot_ref,
                stage="RESERVATION_FAILED",
                snapshot_verified=False,
                upload_order=None,
                name=name,
                description=description,
                creator_id=creator_id,
                incarnation_id=incarnation_id,
            )
            logger.exception(
                "Local Skill security handoff reservation failed tenant=%s "
                "env=%s bot_id=%s owner_id=%s actor_id=%s skill_id=%s "
                "observation_id=%s digest=%s snapshot_ref=%s",
                tenant,
                env,
                bot_id,
                owner_id,
                actor_id,
                skill_id,
                observation_id,
                digest,
                snapshot_ref,
            )
            raise
        if cancelled.is_set():
            return
        snapshot_failure_stage = "SNAPSHOT_UNCERTAIN"
        try:
            if cancelled.is_set():
                return
            created = self._objects.create_object_if_absent(snapshot_ref, canonical_zip)
            if cancelled.is_set():
                return
            if created not in (
                ObjectCreateResult.CREATED,
                ObjectCreateResult.ALREADY_EXISTS,
            ):
                raise RuntimeError(f"immutable snapshot create is uncertain: {created}")
            stored = self._objects.read_object(snapshot_ref)
            if cancelled.is_set():
                return
            if stored.status is not ObjectReadStatus.FOUND or stored.content is None:
                raise RuntimeError("immutable snapshot readback is unavailable")
            if stored.content != canonical_zip:
                snapshot_failure_stage = "SNAPSHOT_DIGEST_MISMATCH"
                raise RuntimeError("immutable snapshot readback did not match upload")
        except Exception:
            self._record_failure(
                tenant=tenant,
                env=env,
                bot_id=bot_id,
                owner_id=owner_id,
                actor_id=actor_id,
                skill_id=skill_id,
                observation_id=observation_id,
                digest=digest,
                snapshot_ref=snapshot_ref,
                stage=snapshot_failure_stage,
                snapshot_verified=False,
                upload_order=upload_order,
                incarnation_id=incarnation_id,
                name=name,
                description=description,
                creator_id=creator_id,
            )
            logger.exception(
                "Local Skill security snapshot failed tenant=%s env=%s bot_id=%s "
                "skill_id=%s observation_id=%s snapshot_ref=%s digest=%s",
                tenant,
                env,
                bot_id,
                skill_id,
                observation_id,
                snapshot_ref,
                digest,
            )
            raise
        try:
            if cancelled.is_set():
                return
            identity = self._repository.ensure_for_asset(
                env=env,
                bot_id=bot_id,
                owner_id=owner_id,
                skill_id=skill_id,
                creator_id=creator_id,
                incarnation_id=incarnation_id,
            )
            if cancelled.is_set():
                return
            self._repository.record_observation(
                env=env,
                bot_id=bot_id,
                owner_id=owner_id,
                skill_id=skill_id,
                observation_id=observation_id,
                sha256=digest,
                snapshot_ref=snapshot_ref,
                modifier_id=actor_id,
                name=name,
                description=description,
                upload_order=upload_order,
                cancelled=cancelled,
                incarnation_id=incarnation_id,
            )
        except ObservationHandoffCancelledError:
            return
        except Exception:
            self._record_failure(
                tenant=tenant,
                env=env,
                bot_id=bot_id,
                owner_id=owner_id,
                actor_id=actor_id,
                skill_id=skill_id,
                observation_id=observation_id,
                digest=digest,
                snapshot_ref=snapshot_ref,
                stage="IDENTITY_FAILED",
                snapshot_verified=True,
                name=name,
                description=description,
                creator_id=creator_id,
                upload_order=upload_order,
                incarnation_id=incarnation_id,
            )
            logger.exception(
                "Local Skill security identity/observation failed tenant=%s "
                "env=%s bot_id=%s skill_id=%s observation_id=%s "
                "snapshot_ref=%s digest=%s",
                tenant,
                env,
                bot_id,
                skill_id,
                observation_id,
                snapshot_ref,
                digest,
            )
            raise
        try:
            enqueue_security_scan_task(
                self._queue,
                tenant=tenant,
                env=env,
                observation_id=observation_id,
            )
        except Exception:
            logger.exception(
                "Local Skill security scan enqueue failed tenant=%s env=%s "
                "observation_id=%s",
                tenant,
                env,
                observation_id,
            )
            try:
                self._repository.mark_scan_error(
                    env=env,
                    observation_id=observation_id,
                    error_code="TASK_ENQUEUE_FAILED",
                    error_message="Security scan task enqueue failed",
                )
            except Exception:
                logger.exception(
                    "Local Skill security scan enqueue error could not be saved "
                    "tenant=%s env=%s observation_id=%s",
                    tenant,
                    env,
                    observation_id,
                )
        try:
            enqueue_security_sync_task(
                self._queue,
                tenant=tenant,
                env=env,
                observation_id=observation_id,
                provider_skill_id=identity.provider_skill_id,
            )
        except Exception:
            logger.exception(
                "Local Skill security sync enqueue failed tenant=%s env=%s "
                "observation_id=%s",
                tenant,
                env,
                observation_id,
            )
            try:
                self._repository.mark_sync_error(
                    env=env,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    skill_id=skill_id,
                    observation_id=observation_id,
                    incarnation_id=incarnation_id,
                    error_code="TASK_ENQUEUE_FAILED",
                    error_message="Security metadata sync task enqueue failed",
                )
            except Exception:
                logger.exception(
                    "Local Skill security sync enqueue error could not be saved "
                    "tenant=%s env=%s observation_id=%s",
                    tenant,
                    env,
                    observation_id,
                )

    def record_handoff_timeout(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        skill_id: int,
        observation_id: str,
        digest: str,
        incarnation_id: str,
        name: str,
        description: str,
        creator_id: str | None,
    ) -> None:
        tenant = get_current_avernet_tenant()
        env = self._env_provider()
        self._record_failure(
            tenant=tenant,
            env=env,
            bot_id=bot_id,
            owner_id=owner_id,
            actor_id=actor_id,
            skill_id=skill_id,
            observation_id=observation_id,
            digest=digest,
            snapshot_ref=self._snapshot_ref(tenant, env, observation_id),
            stage="HANDOFF_TIMEOUT",
            snapshot_verified=False,
            upload_order=None,
            incarnation_id=incarnation_id,
            name=name,
            description=description,
            creator_id=creator_id,
        )

    @staticmethod
    def _snapshot_ref(tenant: str, env: str, observation_id: str) -> str:
        return (
            f"aidesktop/aidesktop_{env}/bolt_shared/skills-upload/"
            f"local-security/{tenant}/{observation_id}.zip"
        )

    def _record_failure(
        self,
        *,
        tenant: str,
        env: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        skill_id: int,
        observation_id: str,
        digest: str,
        snapshot_ref: str,
        stage: str,
        snapshot_verified: bool,
        incarnation_id: str,
        name: str,
        description: str,
        creator_id: str | None,
        upload_order: int | None,
    ) -> None:
        try:
            self._repository.record_handoff_failure(
                env=env,
                bot_id=bot_id,
                owner_id=owner_id,
                actor_id=actor_id,
                skill_id=skill_id,
                observation_id=observation_id,
                sha256=digest,
                snapshot_ref=snapshot_ref,
                stage=stage,
                snapshot_verified=snapshot_verified,
                incarnation_id=incarnation_id,
                name=name,
                description=description,
                creator_id=creator_id,
                upload_order=upload_order,
            )
        except Exception:
            logger.exception(
                "Local Skill security handoff failure could not be persisted "
                "tenant=%s env=%s bot_id=%s owner_id=%s actor_id=%s "
                "skill_id=%s observation_id=%s digest=%s snapshot_ref=%s "
                "stage=%s snapshot_verified=%s",
                tenant,
                env,
                bot_id,
                owner_id,
                actor_id,
                skill_id,
                observation_id,
                digest,
                snapshot_ref,
                stage,
                snapshot_verified,
            )

    def _first_uploader(
        self,
        *,
        bot_id: str,
        owner_id: str,
        skill_id: int,
        env: str,
        incarnation_id: str,
    ) -> str | None:
        try:
            existing = self._repository.get_identity_for_asset(
                env=env,
                bot_id=bot_id,
                owner_id=owner_id,
                skill_id=skill_id,
                incarnation_id=incarnation_id,
            )
        except Exception:
            logger.exception(
                "Local Skill original-uploader identity lookup failed "
                "bot_id=%s skill_id=%s",
                bot_id,
                skill_id,
            )
            existing = None
        if existing is not None:
            return existing.creator_id
        logger.warning(
            "Local Skill original uploader remains unknown bot_id=%s skill_id=%s",
            bot_id,
            skill_id,
        )
        return None
