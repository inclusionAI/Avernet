"""PublishApproval responsibilities for the composed service."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from secbaas.community.core.repository.bot import BotRecord
    from secbaas.community.core.repository.publish import PublishRecord

from secbaas.community.api.bot_manage import BotStatus
from secbaas.community.api.publish_manage import (
    BatchStatus,
    BotPublishSummary,
    PublishConfig,
    PublishNotFoundError,
    PublishResponse,
    PublishStage,
    PublishStatus,
    PublishType,
)
from secbaas.community.core.repository.publish_batch import (
    PublishBatchRecord,
)
from secbaas.community.logger import get_logger

from ._publish_types import _extra_config_to_publish_config

logger = get_logger("core-service")


class PublishApproval:
    """Internal mixin; dependencies and sibling methods belong to the host service."""

    def _cleanup_pending_clone_on_update_failure(
        self,
        tenant: str,
        publish_id: int,
        operator: str,
    ) -> None:
        """Abandon the still-PENDING clone reserved by an UPDATE publish.

        An UPDATE publish reserves its deployment slot by cloning the source
        bot row into a PENDING bot row (create_publish Step 4.5) and recording
        the clone id in ``baas_publish.extra_config["target_bot_id"]``. The
        clone must not stay live once its publish reaches a terminal outcome,
        or it permanently occupies the unique key slot
        (tenant, bot_uuid, env, status=PENDING, is_deleted=0) and every later
        publish for the bot_uuid fails with a duplicate key violation.

        Callers cover every terminal path of an UPDATE publish: stage and
        execution failures (FAILED), the create-time orphan auto-clean and
        stale auto-fail, rejection (REJECTED), revocation (REVOKED), and the
        admin force-success abandonment. Per design D2 the call happens
        BEFORE the terminal status write, so a cleanup failure leaves the
        publish non-terminal and the error surfaces.

        Defensive no-ops: the publish is missing, its extra_config has no
        ``target_bot_id`` pointer (crash window between the clone insert and
        the pointer write), the target row is gone, or the target is no
        longer live PENDING — another terminal path may have resolved it or
        the transfer may have promoted it.

        Also soft-deletes any bot_device_rel rows on the clone (should
        always be zero since device transfer happens only at SUCCESS, but the
        cleanup is harmless and future-proof).
        """

        env = self._get_current_env()
        publish_repo = self._publish_repo
        bot_repo = self._bot_repo
        rel_repo = self._rel_repo

        publish_record = publish_repo.get_by_id(publish_id, tenant=tenant, env=env)
        if publish_record is None:
            return

        target_bot_id = (publish_record.extra_config or {}).get("target_bot_id")
        if target_bot_id is None:
            return

        target_bot = bot_repo.get_by_id(target_bot_id, tenant=tenant, env=env)
        if target_bot is None:
            return
        if target_bot.status != BotStatus.PENDING.value:
            return

        try:
            rel_repo.soft_delete_by_bot_id(
                bot_id=target_bot_id,
                tenant=tenant,
                env=env,
                modifier=operator,
            )
        except Exception:
            logger.warning(
                f"[cleanup_pending] Failed to soft-delete device relationships "
                f"for orphan PENDING bot {target_bot_id} (may have none)",
                exc_info=True,
            )

        bot_repo.soft_delete(
            bot_id=target_bot_id,
            tenant=tenant,
            env=env,
            modifier=operator,
        )
        logger.info(
            f"[cleanup_pending] Soft-deleted still-PENDING clone bot {target_bot_id} "
            f"abandoned by UPDATE publish {publish_id} terminal resolution"
        )

    def _owns_live_device_binding(self, bot_id: int, tenant: str, env: str) -> bool:
        """True when the generation still holds device ownership nothing else does.

        Device ownership is authoritative in ``baas_bot_device_rel`` (the
        restore-resolution precedent, #2648: live rel rows identify the record
        generation that actually served devices). A rel row is *ownership* only
        while it is the device's live binding; a rel whose device is already
        bound by another live generation is a stale duplicate left by a
        corruption shape and may be tombstoned together with the swept row.
        """
        for rel in self._rel_repo.list_by_bot_id(bot_id, tenant, env):
            binding = self._rel_repo.get_by_device_uuid(rel.device_uuid, tenant, env)
            if binding is not None and binding.bot_id == bot_id:
                return True
        return False

    def _can_transition(self, current_status: str, action: str) -> bool:
        """Check if state transition is valid."""
        return (current_status, action) in self.TRANSITIONS

    def _get_next_status(self, current_status: str, action: str) -> str | None:
        """Get next status after transition."""
        return self.TRANSITIONS.get((current_status, action))

    def _get_current_stage(self, tenant: str, publish_id: int) -> str | None:
        """Get current pipeline stage from the first non-completed batch.

        Returns the stage of the first batch that hasn't completed.
        Returns None if all batches are complete or no batches exist.
        """
        env = self._get_current_env()
        batch_repo = self._publish_batch_repo
        batches = batch_repo.list_by_publish_id(publish_id, tenant, env)

        if not batches:
            return None

        # Find first non-COMPLETED batch
        for batch in batches:
            if batch.status != BatchStatus.COMPLETED.value:
                return batch.stage

        # All batches complete
        return PublishStage.SUCCESS.value

    def _get_pending_batches(
        self, tenant: str, publish_id: int
    ) -> tuple[str | None, list[PublishBatchRecord]]:
        """Get pending batches (first non-COMPLETED stage) in ONE query.

        Returns (current_stage, batches) tuple.
        This avoids the circular dependency of querying batches twice.
        """
        env = self._get_current_env()
        batch_repo = self._publish_batch_repo
        all_batches = batch_repo.list_by_publish_id(publish_id, tenant, env)

        if not all_batches:
            return None, []

        # Find first non-COMPLETED batch to determine current stage
        pending_stage = None
        for batch in all_batches:
            if batch.status != BatchStatus.COMPLETED.value:
                pending_stage = batch.stage
                break

        if pending_stage is None:
            # All complete
            return PublishStage.SUCCESS.value, []

        # Filter batches by the pending stage
        pending_batches = [b for b in all_batches if b.stage == pending_stage]
        return pending_stage, pending_batches

    def _check_all_batches_complete(self, tenant: str, publish_id: int) -> bool:
        """Check if all batches for a publish are processed (no PENDING or RUNNING).

        Returns True if no batches are PENDING or RUNNING, meaning all stages complete.
        Returns False if there are still batches to process.
        """
        env = self._get_current_env()
        batch_repo = self._publish_batch_repo
        batches = batch_repo.list_by_publish_id(publish_id, tenant, env)

        if not batches:
            return False

        # Check if any batch is still pending or running
        for batch in batches:
            if batch.status in (BatchStatus.PENDING.value, BatchStatus.RUNNING.value):
                return False

        return True

    def _should_auto_complete(
        self, tenant: str, publish_id: int, config: PublishConfig
    ) -> bool:
        """Check if publish should auto-complete.

        Returns True if:
        - auto_complete config is True
        - All batches are COMPLETED (no PENDING, RUNNING, or FAILED)
        """
        if not config.auto_complete:
            return False

        env = self._get_current_env()
        batch_repo = self._publish_batch_repo
        batches = batch_repo.list_by_publish_id(publish_id, tenant, env)

        if not batches:
            return False

        # Auto-complete only when ALL batches are COMPLETED
        # - PENDING/RUNNING means there are more stages to execute
        # - FAILED means something went wrong, need manual intervention
        for batch in batches:
            if batch.status != BatchStatus.COMPLETED.value:
                return False

        return True

    def _get_publish_and_bot_record(
        self, tenant: str, publish_id: int
    ) -> tuple[PublishRecord | None, BotRecord | None]:
        """Get publish record and bot by ID with tenant verification.

        Returns (record, bot) tuple. Returns (None, None) if not found
        or tenant mismatch. The bot read is always needed for tenant
        verification, so we return it to avoid callers re-fetching it.

        For DESTROY publishes, the bot may have been soft-deleted after
        completion. In that case, we still need to find it for tenant
        verification — a soft-deleted bot matching tenant+env is still
        a valid ownership proof.
        """
        env = self._get_current_env()
        repo = self._publish_repo
        publish_record = repo.get_by_id(publish_id, tenant=tenant, env=env)

        if publish_record is None:
            return None, None

        bot_repo = self._bot_repo
        bot_record = bot_repo.get_by_id_including_deleted(
            publish_record.bot_id, tenant=tenant, env=env
        )
        if bot_record is None:
            logger.warning(
                f"Tenant mismatch for publish: tenant={tenant}, publish={publish_id}"
            )
            return None, None

        return publish_record, bot_record

    async def get_publish_bot_uuid(self, *, tenant: str, publish_id: int) -> str:
        """Resolve publish_id to bot_uuid with tenant verification.

        Two-hop lookup: publish_id -> PublishRecord -> BotRecord.bot_uuid.
        Uses get_by_id_including_deleted for the bot lookup to handle DESTROY
        publishes where the bot may have been soft-deleted.

        Raises PublishNotFoundError if the publish does not exist, the tenant
        does not match, or the associated bot record cannot be found.
        """
        env = self._get_current_env()
        publish_record = self._publish_repo.get_by_id(
            publish_id, tenant=tenant, env=env
        )
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        bot_record = self._bot_repo.get_by_id_including_deleted(
            publish_record.bot_id, tenant=tenant, env=env
        )
        if bot_record is None:
            raise PublishNotFoundError(publish_id)

        return bot_record.bot_uuid

    def _build_publish_response(self, publish_record: PublishRecord) -> PublishResponse:
        """Build PublishResponse from a publish record without fetching bot_record.

        Use this when the caller already has publish_record and only needs the
        response — avoids the extra DB query to fetch bot_record for tenant
        verification (which was already done earlier in the call chain).
        """
        current_stage = self._get_current_stage(
            publish_record.tenant, publish_record.id
        )
        return PublishResponse(
            id=publish_record.id,
            bot_id=publish_record.bot_id,
            publish_type=publish_record.publish_type,
            status=publish_record.status,
            stage=current_stage,
            extra_config=_extra_config_to_publish_config(publish_record.extra_config),
            creator=publish_record.creator,
            modifier=publish_record.modifier,
            gmt_create=publish_record.gmt_create,
            gmt_modified=publish_record.gmt_modified,
        )

    async def _refresh_publish_response(
        self, tenant: str, publish_id: int
    ) -> PublishResponse:
        """Re-read publish record and build response (skips bot fetch).

        Use after status updates where the publish record needs a fresh read
        but bot_record was already verified earlier in the call chain.
        Saves 1 DB query vs calling get_publish() which also fetches bot_record.
        """
        env = self._get_current_env()
        repo = self._publish_repo
        publish_record = repo.get_by_id(publish_id, tenant=tenant, env=env)
        if publish_record is None:
            raise PublishNotFoundError(publish_id)
        return self._build_publish_response(publish_record)

    async def get_publish(self, tenant: str, publish_id: int) -> PublishResponse | None:
        """Get publish by ID with tenant verification (full, for API responses)."""
        env = self._get_current_env()
        logger.info(
            f"[get_publish] tenant={tenant}, env={env}, publish_id={publish_id}"
        )

        publish_record, bot_record = self._get_publish_and_bot_record(
            tenant, publish_id
        )
        if publish_record is None:
            return None

        current_stage = self._get_current_stage(tenant, publish_id)
        return PublishResponse(
            id=publish_record.id,
            bot_id=publish_record.bot_id,
            publish_type=publish_record.publish_type,
            status=publish_record.status,
            stage=current_stage,
            extra_config=_extra_config_to_publish_config(publish_record.extra_config),
            creator=publish_record.creator,
            modifier=publish_record.modifier,
            gmt_create=publish_record.gmt_create,
            gmt_modified=publish_record.gmt_modified,
        )

    async def approve_stage(
        self,
        tenant: str,
        publish_id: int,
        operator: str,
        comment: str | None = None,
        _called_internally: bool = False,
    ) -> PublishResponse:
        """
        Approve current stage and proceed.

        Per D-02: Handles both initial approval (PENDING -> ACTIVE) and
        stage-gate approval (APPROVING -> ACTIVE).

        Per D-10: Approver can also revoke after approval.

        Auto-execute: When transitioning from PENDING to ACTIVE, automatically
        executes all stages until completion or approval gate.

        Args:
            tenant: Tenant name for isolation
            publish_id: Publish to approve
            operator: User ID of operator
            comment: Optional approval comment
            _called_internally: Internal flag — when True, bypasses
                the auto_approve guard. Set by _auto_approve_publish loop.
        """
        env = self._get_current_env()
        logger.info(
            f"[approve_stage] publish_id={publish_id}, operator={operator}, "
            f"comment={comment}"
        )

        # Get publish and verify (lightweight — no bot status calc needed)
        publish_record, bot_record = self._get_publish_and_bot_record(
            tenant, publish_id
        )
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        # Guard: ignore manual /approve calls when auto_approve is active
        extra_config = publish_record.extra_config or {}
        if extra_config.get("auto_approve") and not _called_internally:
            logger.info(
                f"[approve_stage] publish_id={publish_id} auto_approve is active, "
                f"ignoring manual approve call"
            )
            return await self._refresh_publish_response(tenant, publish_id)

        current_status = publish_record.status

        if current_status == PublishStatus.SUCCESS.value:
            logger.info(
                f"[approve_stage] publish_id={publish_id} already SUCCESS, no-op"
            )
            return await self._refresh_publish_response(tenant, publish_id)

        if current_status == PublishStatus.ACTIVE.value:
            logger.info(
                f"[approve_stage] publish_id={publish_id} already ACTIVE, "
                f"continuing execution"
            )
            await self._auto_execute_stages(tenant, publish_id, operator)
            return await self._refresh_publish_response(tenant, publish_id)

        if current_status == PublishStatus.APPROVING.value:
            logger.info(
                f"[approve_stage] publish_id={publish_id} APPROVING, "
                f"transitioning to ACTIVE and continuing execution"
            )
            new_status = self._get_next_status(current_status, "approve")
            if new_status is None:
                raise ValueError(
                    f"No valid transition from {current_status} with action 'approve'"
                )
            repo = self._publish_repo
            repo.update_status(
                publish_id=publish_id,
                tenant=tenant,
                env=env,
                status=new_status,
                modifier=operator,
            )
            logger.info(
                f"Publish {publish_id} approved by {operator}, new status: {new_status}"
            )
            await self._auto_execute_stages(tenant, publish_id, operator)
            return await self._refresh_publish_response(tenant, publish_id)

        # Validate state transition
        if not self._can_transition(current_status, "approve"):
            raise ValueError(
                f"Cannot approve publish in status '{current_status}'. "
                f"Expected status: PENDING or APPROVING"
            )

        # Update status
        new_status = self._get_next_status(current_status, "approve")
        if new_status is None:
            raise ValueError(
                f"No valid transition from {publish_record.status} with action 'approve'"
            )
        repo = self._publish_repo
        repo.update_status(
            publish_id=publish_id,
            tenant=tenant,
            env=env,
            status=new_status,
            modifier=operator,
        )

        logger.info(
            f"Publish {publish_id} approved by {operator}, new status: {new_status}"
        )

        # Auto-execute when transitioning to ACTIVE (both PENDING→ACTIVE and APPROVING→ACTIVE)
        if new_status == PublishStatus.ACTIVE.value:
            logger.info(
                f"[approve_stage] Auto-executing stage for publish {publish_id}"
            )
            await self._auto_execute_stages(tenant, publish_id, operator)

        return await self._refresh_publish_response(tenant, publish_id)

    async def _auto_execute_stages(
        self, tenant: str, publish_id: int, operator: str
    ) -> None:
        """Dispatch current stage's batches and return.

        With async start hooks, execute_stage() returns immediately after
        dispatching devices. Subsequent stage execution is triggered by the
        callback handler when the current stage completes.
        For fully synchronous batches (DESTROY, SCALE_DOWN, all no-hook),
        execute_stage() handles stage advancement inline.
        """
        publish_record, bot_record = self._get_publish_and_bot_record(
            tenant, publish_id
        )

        if (
            publish_record is None
            or publish_record.status != PublishStatus.ACTIVE.value
        ):
            return

        # Check if there are pending batches
        current_stage, batches = self._get_pending_batches(tenant, publish_id)
        if not batches:
            return

        # Execute current stage (pass pre-fetched data to avoid redundant DB queries)
        try:
            await self.execute_stage(
                tenant,
                publish_id,
                operator,
                publish_record=publish_record,
                bot_record=bot_record,
                current_stage=current_stage,
                batches=batches,
            )
        except Exception as e:
            logger.error(
                f"Auto-execute failed for publish {publish_id}: {e}",
                exc_info=True,
            )

    async def reject_publish(
        self, tenant: str, publish_id: int, operator: str, reason: str
    ) -> PublishResponse:
        """
        Reject publish at any approval gate.

        Per D-02, D-10: Can reject from PENDING or APPROVING status.
        Marks publish as REJECTED - no further progress allowed.

        Note: For DESTROY publishes, the bot remains in DESTROYING status.
        The publish can be re-approved to continue the destruction.
        Bot status restoration is intentionally NOT performed since DESTROY
        is a terminal operation that should eventually complete.
        """
        env = self._get_current_env()
        logger.info(
            f"[reject_publish] publish_id={publish_id}, operator={operator}, "
            f"reason={reason}"
        )

        # Get publish and verify
        publish_record, bot_record = self._get_publish_and_bot_record(
            tenant, publish_id
        )
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        # Validate state transition (can reject from PENDING or APPROVING)
        if publish_record.status not in ("PENDING", "APPROVING"):
            raise ValueError(
                f"Cannot reject publish in status '{publish_record.status}'. "
                f"Expected status: PENDING or APPROVING"
            )

        # Abandon the still-PENDING clone before the terminal REJECTED write
        # (design D2): a cleanup failure propagates and leaves the publish
        # non-terminal — the clone must never hide behind a terminal status.
        if publish_record.publish_type == PublishType.UPDATE.value:
            self._cleanup_pending_clone_on_update_failure(
                tenant=tenant,
                publish_id=publish_id,
                operator=operator,
            )

        # Update status to REJECTED
        repo = self._publish_repo
        repo.update_status(
            publish_id=publish_id,
            tenant=tenant,
            env=env,
            status="REJECTED",
            modifier=operator,
        )

        logger.info(f"Publish {publish_id} rejected by {operator}: {reason}")

        return await self._refresh_publish_response(tenant, publish_id)

    async def revoke_publish(
        self, tenant: str, publish_id: int, operator: str, reason: str | None = None
    ) -> PublishResponse:
        """
        Revoke publish after approval but before next stage begins.

        Per D-10: Can only revoke from APPROVING status (after approval received,
        before execution resumes). Marks publish as REVOKED.

        Note: For DESTROY publishes, the bot remains in DESTROYING status.
        The publish can be re-approved to continue the destruction.
        Bot status restoration is intentionally NOT performed since DESTROY
        is a terminal operation that should eventually complete.
        """
        env = self._get_current_env()
        logger.info(
            f"[revoke_publish] publish_id={publish_id}, operator={operator}, "
            f"reason={reason}"
        )

        # Get publish and verify
        publish_record, bot_record = self._get_publish_and_bot_record(
            tenant, publish_id
        )
        if publish_record is None:
            raise PublishNotFoundError(publish_id)

        # Validate state transition (can only revoke from APPROVING)
        if publish_record.status != "APPROVING":
            raise ValueError(
                f"Cannot revoke publish in status '{publish_record.status}'. "
                f"Expected status: APPROVING"
            )

        # Abandon the still-PENDING clone before the terminal REVOKED write
        # (design D2): a cleanup failure propagates and leaves the publish
        # non-terminal — the clone must never hide behind a terminal status.
        if publish_record.publish_type == PublishType.UPDATE.value:
            self._cleanup_pending_clone_on_update_failure(
                tenant=tenant,
                publish_id=publish_id,
                operator=operator,
            )

        # Update status to REVOKED
        repo = self._publish_repo
        repo.update_status(
            publish_id=publish_id,
            tenant=tenant,
            env=env,
            status="REVOKED",
            modifier=operator,
        )

        logger.info(f"Publish {publish_id} revoked by {operator}: {reason}")

        return await self._refresh_publish_response(tenant, publish_id)

    async def retry_publish(
        self,
        tenant: str,
        publish_id: int,
        operator: str,
        request_id: str,
        config: PublishConfig | None = None,
    ) -> PublishResponse:
        """Retry a failed publish by creating a new publish.

        Creates a new publish with the same bot_id, publish_type, and config
        as the original failed publish. The new publish starts in PENDING status.

        Args:
            tenant: Tenant name for isolation
            publish_id: ID of the FAILED publish to retry
            operator: User retrying the publish
            request_id: Request ID for correlation
            config: Optional new config (uses original if not provided)

        Returns:
            PublishResponse for the newly created publish

        Raises:
            PublishNotFoundError: Original publish not found
            ValueError: Original publish is not in FAILED status
            ValueError: Bot has concurrent active publish
        """
        logger.info(
            f"[retry_publish] original_publish_id={publish_id}, operator={operator}, "
            f"request_id={request_id}"
        )

        original, _ = self._get_publish_and_bot_record(tenant, publish_id)
        if original is None:
            raise PublishNotFoundError(publish_id)

        # Verify original is in FAILED status
        if original.status != PublishStatus.FAILED.value:
            raise ValueError(
                f"Cannot retry publish in status '{original.status}'. "
                f"Retry is only valid for FAILED publishes. "
                f"Create a new publish to retry from other terminal states."
            )

        # Create new publish with same parameters
        new_publish = await self.create_publish(
            tenant=tenant,
            bot_id=original.bot_id,
            publish_type=PublishType(original.publish_type),
            operator=operator,
            request_id=request_id,
            config=config or _extra_config_to_publish_config(original.extra_config),
        )

        logger.info(
            f"[retry_publish] Created new publish {new_publish.id} "
            f"to retry failed publish {publish_id}"
        )

        return new_publish

    async def list_publishes(
        self,
        tenant: str,
        bot_id: int | None = None,
        status: PublishStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[PublishResponse]:
        """List publishes for tenant with optional filtering."""
        env = self._get_current_env()
        logger.info(
            f"[list_publishes] tenant={tenant}, env={env}, bot={bot_id}, status={status}"
        )

        repo = self._publish_repo
        records = (
            repo.list_by_bot_id(bot_id=bot_id, tenant=tenant, env=env) if bot_id else []
        )

        results = []
        for publish_record in records:
            # Verify tenant
            bot = await self._bot_service.get_bot(
                tenant=tenant, bot_id=publish_record.bot_id, include_status=False
            )
            if bot is None:
                continue

            if status is not None and publish_record.status != status.value:
                continue

            current_stage = self._get_current_stage(tenant, publish_record.id)
            results.append(
                PublishResponse(
                    id=publish_record.id,
                    bot_id=publish_record.bot_id,
                    publish_type=publish_record.publish_type,
                    status=publish_record.status,
                    stage=current_stage,
                    extra_config=_extra_config_to_publish_config(
                        publish_record.extra_config
                    ),
                    creator=publish_record.creator,
                    modifier=publish_record.modifier,
                    gmt_create=publish_record.gmt_create,
                    gmt_modified=publish_record.gmt_modified,
                )
            )

        # Simple pagination
        start = (page - 1) * page_size
        end = start + page_size

        return results[start:end]

    async def list_publishes_by_bot_uuid(
        self,
        tenant: str,
        bot_uuid: str,
    ) -> list[BotPublishSummary]:
        """List every publish workflow tied to a bot_uuid, newest first.

        Backs ``GET /api/v1/bots/{bot_uuid}/publishes``. A bot_uuid may map to
        several bot records (distinct statuses across its lifecycle); the union
        of their publishes is returned so an idempotency caller can difference
        the bot's complete workflow history — including workflows already in a
        terminal state — not just the currently-active one. Returns ``[]`` when
        the bot_uuid is unknown (the router turns that into a 404).

        Soft-deleted bot records are included: a successful DESTROY soft-deletes
        the bot, and its DESTROY workflow must stay visible so a crash-resumed
        destroy operation adopts it instead of re-issuing against a gone bot.
        """
        env = self._get_current_env()
        bot_records = self._bot_repo.list_by_bot_uuid_including_deleted(
            bot_uuid=bot_uuid, tenant=tenant, env=env
        )
        if not bot_records:
            return []

        summaries: list[BotPublishSummary] = []
        for bot_record in bot_records:
            for publish_record in self._publish_repo.list_by_bot_id(
                bot_id=bot_record.id, tenant=tenant, env=env
            ):
                summaries.append(
                    BotPublishSummary(
                        id=publish_record.id,
                        bot_id=publish_record.bot_id,
                        publish_type=publish_record.publish_type,
                        status=publish_record.status,
                        gmt_create=publish_record.gmt_create,
                    )
                )

        # Newest first by workflow id (monotonic), so adopt-by-query picks the
        # most recent matching workflow deterministically.
        summaries.sort(key=lambda s: s.id, reverse=True)
        return summaries
