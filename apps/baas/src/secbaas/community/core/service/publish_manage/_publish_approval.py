"""Publish workflow: approval responsibilities."""

from __future__ import annotations

from secbaas.community.api.publish_manage import (
    PublishConfig,
    PublishNotFoundError,
    PublishResponse,
    PublishStatus,
    PublishType,
)
from secbaas.community.logger import get_logger

from ._publish_common import _extra_config_to_publish_config

logger = get_logger("core-service")


class PublishApprovalMixin:
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
        env = self._current_env()
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
        env = self._current_env()
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
        env = self._current_env()
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
