"""Re-enqueue durable Local Skill observations abandoned by task delivery."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Callable

from agentclaw.community.core.repository.protocols.local_skill_security_observation import (
    AssetIncarnationMismatchError,
    LocalSkillSecurityObservationRepositoryProtocol,
)
from agentclaw.community.core.skill_center.local_skill_security_observation import (
    LocalSkillSecurityHandoffAttempt,
)
from agentclaw.community.core.skill_center.services.local_skill_security_tasks import (
    enqueue_security_scan_task,
    enqueue_security_sync_task,
)
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.plugin_api.object_storage import (
    ImmutableObjectStorageCapability,
    ObjectReadStatus,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


logger = logging.getLogger(__name__)
PAGE_SIZE = 100
RECONCILE_INTERVAL_SECONDS = 60


class LocalSkillSecurityTaskReconciler(LifecycleBase):
    """Observation DB is authoritative when a queue row times out or is lost.

    A live task key joins the existing work. A terminal queue task releases the
    key, allowing a new row for the same observation. Ambiguous external POSTs
    are excluded by the repository and never submitted automatically again.
    """

    def __init__(
        self,
        *,
        repository: LocalSkillSecurityObservationRepositoryProtocol,
        snapshots: ImmutableObjectStorageCapability,
        queue: TaskQueueService,
        env_provider: Callable[[], str],
        worker_enabled: bool = True,
        interval_seconds: int = RECONCILE_INTERVAL_SECONDS,
    ) -> None:
        self._repository = repository
        self._snapshots = snapshots
        self._queue = queue
        self._env_provider = env_provider
        self._worker_enabled = worker_enabled
        self._interval_seconds = interval_seconds
        self._loop_task: asyncio.Task[None] | None = None

    def reconcile_once(self) -> None:
        env = self._env_provider()
        for tenant in self._repository.list_recovery_tenants(env=env):
            with avernet_tenant_scope(tenant):
                self._reconcile_tenant(tenant, env)

    def _reconcile_tenant(self, tenant: str, env: str) -> None:
        handoff_cursor: str | None = None
        while True:
            page = self._repository.list_repairable_handoffs(
                env=env, after_observation_id=handoff_cursor, limit=PAGE_SIZE
            )
            for handoff in page:
                try:
                    self._repair_handoff(env, handoff)
                except Exception:
                    logger.exception(
                        "Local Skill verified handoff repair failed "
                        "tenant=%s env=%s observation_id=%s",
                        tenant,
                        env,
                        handoff.observation_id,
                    )
            if len(page) < PAGE_SIZE:
                break
            handoff_cursor = page[-1].observation_id

        scan_cursor: str | None = None
        while True:
            page = self._repository.list_recoverable_scans(
                env=env, after_observation_id=scan_cursor, limit=PAGE_SIZE
            )
            for observation in page:
                enqueue_security_scan_task(
                    self._queue,
                    tenant=tenant,
                    env=env,
                    observation_id=observation.observation_id,
                )
            if len(page) < PAGE_SIZE:
                break
            scan_cursor = page[-1].observation_id

        sync_cursor: str | None = None
        while True:
            page = self._repository.list_pending_sync_identities(
                env=env, after_provider_skill_id=sync_cursor, limit=PAGE_SIZE
            )
            for identity in page:
                assert identity.sync_target_observation_id is not None
                enqueue_security_sync_task(
                    self._queue,
                    tenant=tenant,
                    env=env,
                    observation_id=identity.sync_target_observation_id,
                    provider_skill_id=identity.provider_skill_id,
                )
            if len(page) < PAGE_SIZE:
                break
            sync_cursor = page[-1].provider_skill_id

    def _repair_handoff(
        self, env: str, handoff: LocalSkillSecurityHandoffAttempt
    ) -> None:
        if (
            self._repository.get_formal_incarnation_id(
                env=env,
                bot_id=handoff.bot_id,
                owner_id=handoff.owner_id,
                skill_id=handoff.skill_id,
            )
            != handoff.incarnation_id
        ):
            self._mark_stale_handoff(
                env, handoff, snapshot_verified=handoff.snapshot_verified
            )
            return
        read = self._snapshots.read_object(handoff.snapshot_ref)
        if read.status is ObjectReadStatus.NOT_FOUND:
            if (
                handoff.stage in {"PENDING", "SNAPSHOT_UNCERTAIN", "HANDOFF_TIMEOUT"}
                and not handoff.snapshot_verified
            ):
                # The original put may still finish after its request timed out.
                # Without a verified write (or writer-exited fact), absence is
                # not proof the immutable snapshot will never appear.
                return
            self._repository.record_handoff_failure(
                env=env,
                bot_id=handoff.bot_id,
                owner_id=handoff.owner_id,
                actor_id=handoff.actor_id,
                skill_id=handoff.skill_id,
                incarnation_id=handoff.incarnation_id,
                observation_id=handoff.observation_id,
                sha256=handoff.sha256,
                snapshot_ref=handoff.snapshot_ref,
                stage="REPAIR_SNAPSHOT_MISSING",
                snapshot_verified=handoff.snapshot_verified,
            )
            return
        if read.status is not ObjectReadStatus.FOUND or read.content is None:
            return
        digest = "sha256:" + hashlib.sha256(read.content).hexdigest()
        if digest != handoff.sha256:
            self._repository.record_handoff_failure(
                env=env,
                bot_id=handoff.bot_id,
                owner_id=handoff.owner_id,
                actor_id=handoff.actor_id,
                skill_id=handoff.skill_id,
                incarnation_id=handoff.incarnation_id,
                observation_id=handoff.observation_id,
                sha256=handoff.sha256,
                snapshot_ref=handoff.snapshot_ref,
                stage="REPAIR_SNAPSHOT_MISMATCH",
                snapshot_verified=handoff.snapshot_verified,
            )
            return
        if (
            handoff.name is None
            or handoff.description is None
            or not handoff.order_known
        ):
            return
        if not self._repository.reopen_verified_handoff(
            env=env,
            observation_id=handoff.observation_id,
            verified_sha256=digest,
        ):
            return
        try:
            self._repository.ensure_for_asset(
                env=env,
                bot_id=handoff.bot_id,
                owner_id=handoff.owner_id,
                skill_id=handoff.skill_id,
                incarnation_id=handoff.incarnation_id,
                creator_id=handoff.creator_id,
            )
            self._repository.record_observation(
                env=env,
                bot_id=handoff.bot_id,
                owner_id=handoff.owner_id,
                skill_id=handoff.skill_id,
                incarnation_id=handoff.incarnation_id,
                observation_id=handoff.observation_id,
                sha256=handoff.sha256,
                snapshot_ref=handoff.snapshot_ref,
                modifier_id=handoff.actor_id,
                name=handoff.name,
                description=handoff.description,
                upload_order=handoff.upload_order,
            )
        except AssetIncarnationMismatchError:
            self._mark_stale_handoff(env, handoff, snapshot_verified=True)
        except Exception:
            self._repository.record_handoff_failure(
                env=env,
                bot_id=handoff.bot_id,
                owner_id=handoff.owner_id,
                actor_id=handoff.actor_id,
                skill_id=handoff.skill_id,
                incarnation_id=handoff.incarnation_id,
                observation_id=handoff.observation_id,
                sha256=handoff.sha256,
                snapshot_ref=handoff.snapshot_ref,
                stage="IDENTITY_FAILED",
                snapshot_verified=True,
                name=handoff.name,
                description=handoff.description,
                creator_id=handoff.creator_id,
            )
            raise

    def _mark_stale_handoff(
        self,
        env: str,
        handoff: LocalSkillSecurityHandoffAttempt,
        *,
        snapshot_verified: bool,
    ) -> None:
        self._repository.record_handoff_failure(
            env=env,
            bot_id=handoff.bot_id,
            owner_id=handoff.owner_id,
            actor_id=handoff.actor_id,
            skill_id=handoff.skill_id,
            incarnation_id=handoff.incarnation_id,
            observation_id=handoff.observation_id,
            sha256=handoff.sha256,
            snapshot_ref=handoff.snapshot_ref,
            stage="ASSET_INCARNATION_STALE",
            snapshot_verified=snapshot_verified,
            name=handoff.name,
            description=handoff.description,
            creator_id=handoff.creator_id,
        )

    async def startup(self) -> None:
        if self._worker_enabled:
            self._loop_task = asyncio.create_task(self._loop())

    async def shutdown(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.reconcile_once)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Local Skill security task reconciliation failed: %s",
                    type(exc).__name__,
                )
            await asyncio.sleep(self._interval_seconds)


__all__ = ["LocalSkillSecurityTaskReconciler"]
