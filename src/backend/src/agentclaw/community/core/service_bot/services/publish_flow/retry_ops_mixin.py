"""Failed-flow retry orchestration, mixed into PublishFlowService.

``retry`` rolls a FAILED record back to its pre-failure status (``ext.source_status``)
and re-drives the appropriate durable path — rebuild/verify, online-release re-run,
or a BaaS-level restart — depending on how far the failed attempt had progressed.
Extracted from ``publish_flow_service.py`` to keep that file within the R9 line cap.
"""
from __future__ import annotations

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    PublishNotFoundError,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
    enqueue_online_release,
    enqueue_progress_poll,
    enqueue_verify_flow,
)
from agentclaw.community.log import get_logger

logger = get_logger()

# The pre-failure statuses a FAILED record may be retried from. A record rolls
# back to exactly its ``ext.source_status``; anything outside this set is not a
# retryable scenario.
_RETRYABLE_SOURCE_STATUSES = frozenset({
    PublishStatus.BUILDING,
    PublishStatus.BUILT,
    PublishStatus.VALIDATE_PUB,
    PublishStatus.VALIDATING,
    PublishStatus.ONLINE_PUB,
    PublishStatus.SUCCESS,
})


class RetryOpsMixin:
    """Failed-flow retry orchestration, mixed into PublishFlowService."""

    async def retry(
        self,
        publish_id: int,
        operator: str,
    ) -> PublishFlowResult:
        """Retry a failed publish flow.

        Based on the pre-failure status (ext.source_status), roll back to the corresponding status and
        re-advance the flow:
        - building → roll back to BUILDING, rebuild + verify publish
        - built → roll back to BUILT, re-run verify publish
        - validate_pub → roll back to VALIDATE_PUB, call BaaS restart to retry
        - online_pub → roll back to ONLINE_PUB; if the online release was already
          recorded (BaaS-wait failure) call BaaS restart, otherwise re-run the
          online release work via the online_release task

        Args:
            publish_id: Publish record ID
            operator: Operator

        Returns:
            PublishFlowResult: Retry result

        Raises:
            PublishNotFoundError: Publish record does not exist
            PublishFlowServiceError: Status does not support retry or rollback failed
        """
        logger.info(
            f"[PublishFlowService.retry] called: publish_id={publish_id}, operator={operator}"
        )

        # Step 1: Query the publish record
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)

        # Step 2: Verify the status is FAILED
        if current_status != PublishStatus.FAILED:
            raise PublishFlowServiceError(
                f"Current status {current_status} does not support retry; only FAILED status can be retried"
            )

        # Step 3: Get the pre-failure status from ext
        ext = self._get_latest_ext(publish_id)
        source_status = ext.get("source_status")
        if not source_status:
            raise PublishFlowServiceError(
                f"Publish record is missing pre-failure status info (source_status); cannot retry: publish_id={publish_id}"
            )

        # Step 4: the record rolls back to its pre-failure status (identity of
        # source_status), validated against the retryable set.
        rollback_status = self._resolve_retry_rollback_status(source_status, publish_id)

        # Step 5: roll back FAILED -> rollback_status and set the retry flag.
        # ``retry=True`` tells the progress poll to redirect to
        # ``sync_restart_progress`` (restart mode); the restart branch clears it
        # again if the restart never actually submits.
        ext["retry"] = True
        try:
            self._update_publish_status(
                publish_id=publish_id,
                target_status=rollback_status,
                source_status=PublishStatus.FAILED,
                ext=ext,
            )
        except Exception as e:
            raise PublishFlowServiceError(
                f"Status rollback failed: {rollback_status.value}, error={e}"
            )
        logger.info(
            "[PublishFlowService.retry] Status rolled back: publish_id=%s, FAILED -> %s",
            publish_id, rollback_status.value,
        )

        # Step 6: dispatch by how far the failed attempt had progressed.
        if self._retry_uses_baas_restart(publish_id, rollback_status):
            return await self._retry_via_restart(publish_id, operator, rollback_status)
        if rollback_status in (PublishStatus.VALIDATING, PublishStatus.ONLINE_PUB):
            return self._retry_via_online_release(publish_id, operator, rollback_status)
        return self._retry_via_verify_flow(publish_id, operator, rollback_status)

    def _resolve_retry_rollback_status(
        self, source_status: str, publish_id: int
    ) -> PublishStatus:
        """The status to roll a FAILED record back to *is* its pre-failure status
        (``ext.source_status``); this just validates it against the retryable set.

        A build failure rolls back to BUILDING (not DRAFT): the user-driven
        DRAFT->BUILDING advance already happened and ``verify_flow`` rebuilds from
        there."""
        try:
            rollback_status = PublishStatus(source_status)
        except ValueError:
            rollback_status = None
        if rollback_status not in _RETRYABLE_SOURCE_STATUSES:
            raise PublishFlowServiceError(
                f"Unsupported retry scenario: source_status={source_status}, publish_id={publish_id}"
            )
        return rollback_status

    def _retry_uses_baas_restart(
        self, publish_id: int, rollback_status: PublishStatus
    ) -> bool:
        """A BaaS-level restart is the retry when the mutation already reached BaaS
        and *it* failed:

        * ``VALIDATE_PUB`` — the verify publish was in its BaaS wait;
        * ``SUCCESS`` — a *restart* of a live online bot failed (a SUCCESS record
          only reaches FAILED via a failed restart-sync), so retry re-restarts it;
        * ``ONLINE_PUB`` whose online release the ledger records as done — a
          BaaS-wait failure, so restart. An ONLINE_PUB whose release was never
          recorded means the release *work* itself failed → re-run it instead.
        """
        if rollback_status in (PublishStatus.VALIDATE_PUB, PublishStatus.SUCCESS):
            return True
        return (
            rollback_status == PublishStatus.ONLINE_PUB
            and self.is_current_online_deployment(publish_id)
        )

    async def _retry_via_restart(
        self, publish_id: int, operator: str, rollback_status: PublishStatus
    ) -> PublishFlowResult:
        """Re-deploy via the restart work, run INLINE (not the durable RESTART_TASK
        the /restart endpoint enqueues): the #162 poll enqueued below and the
        restart task would otherwise be claimed in the same worker batch and run
        concurrently, so the poll could read ext.restart before the task wrote it.
        Inline writes ext.restart + re-delivers synchronously so the poll self-drives
        the record out of its *_PUB wait state; execute_restart's runner still gives
        crash-safe idempotent issuance (adopt-by-query on a user re-retry)."""
        stage = self._determine_restart_stage(rollback_status)
        try:
            restart_result = await self.execute_restart(
                publish_id=publish_id, stage=stage.value, operator=operator,
            )
        except Exception as e:
            logger.warning(
                "[PublishFlowService.retry] execute_restart failed: publish_id=%s stage=%s error=%s",
                publish_id, stage.value if stage else None, e,
            )
            restart_result = {"success": False, "message": str(e)}
        success = restart_result.get("success", False)
        if success:
            # The durable poll (retry-flag redirect → sync_restart_progress) drives
            # the record out of its *_PUB wait state.
            enqueue_progress_poll(self._task_queue_service, publish_id=publish_id)
        else:
            # The restart never submitted → clear the retry flag so a stray poll does
            # not keep redirecting to a restart-sync that will never find a workflow.
            self._mutate_and_update_ext(
                publish_id=publish_id, mutator=self._clear_retry_flag
            )
        return PublishFlowResult(
            publish_id=publish_id,
            status=rollback_status,
            action="restart",
            message="Retry submitted (BaaS restart)" if success else f"Retry failed: {restart_result.get('message', 'Unknown error')}",
        )

    def _retry_via_online_release(
        self, publish_id: int, operator: str, rollback_status: PublishStatus
    ) -> PublishFlowResult:
        """Re-open ONLINE_PUB (idempotent if already there) and re-enqueue the
        online_release task, which re-runs the release work."""
        self._advance_status(
            publish_id, PublishStatus.ONLINE_PUB, PublishStatus.VALIDATING
        )
        enqueue_online_release(
            self._task_queue_service, publish_id=publish_id, operator=operator
        )
        return self._retry_submitted_result(publish_id, rollback_status)

    def _retry_via_verify_flow(
        self, publish_id: int, operator: str, rollback_status: PublishStatus
    ) -> PublishFlowResult:
        """BUILDING / BUILT: re-enqueue verify_flow (the build sub-step is skipped
        when already BUILT)."""
        if rollback_status == PublishStatus.BUILDING:
            # A rebuild changes the artifact, so any release op from the failed
            # attempt is superseded — abandon it so the fresh attempt opens new
            # ledger ops rather than resuming/adopting a stale workflow built from
            # the old artifact.
            self._abandon_inflight_operations(
                publish_id, reason="retry rebuild — superseded"
            )
        enqueue_verify_flow(
            self._task_queue_service, publish_id=publish_id, operator=operator
        )
        return self._retry_submitted_result(publish_id, rollback_status)

    @staticmethod
    def _retry_submitted_result(
        publish_id: int, rollback_status: PublishStatus
    ) -> PublishFlowResult:
        return PublishFlowResult(
            publish_id=publish_id,
            status=rollback_status,
            action="process",
            message="Retry submitted, please check progress later",
        )
