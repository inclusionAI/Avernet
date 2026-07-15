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

        # Step 4: Determine the rollback target status and retry action based on
        # source_status. A build failure rolls back to BUILDING (not DRAFT): the
        # user-driven DRAFT -> BUILDING advance already happened, and the verify_flow
        # task rebuilds from BUILDING.
        retry_map = {
            PublishStatus.BUILDING.value: PublishStatus.BUILDING,
            PublishStatus.BUILT.value: PublishStatus.BUILT,
            PublishStatus.VALIDATE_PUB.value: PublishStatus.VALIDATE_PUB,
            PublishStatus.VALIDATING.value: PublishStatus.VALIDATING,
            PublishStatus.ONLINE_PUB.value: PublishStatus.ONLINE_PUB,
            PublishStatus.SUCCESS.value: PublishStatus.SUCCESS,
        }

        rollback_status = retry_map.get(source_status)
        if not rollback_status:
            raise PublishFlowServiceError(
                f"Unsupported retry scenario: source_status={source_status}, publish_id={publish_id}"
            )

        # Step 5: Roll back the status (FAILED -> rollback_status) and set the retry flag
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
            f"[PublishFlowService.retry] Status rolled back: "
            f"publish_id={publish_id}, FAILED -> {rollback_status.value}"
        )

        # Step 6: Execute the retry action. Directly enqueue the corresponding task
        # (no longer via process(), because /process is already read-only for BUILT;
        # a BUILT retry must re-drive verify_flow).
        #
        # A BaaS-level restart applies when the release already reached the BaaS
        # layer and *it* failed: the *_PUB wait states, SUCCESS, and an ONLINE_PUB
        # whose online release was already recorded (poll failure). An ONLINE_PUB
        # whose release was never recorded means the release *work* itself failed,
        # so re-run it via the online_release task instead.
        restart = rollback_status in (
            PublishStatus.VALIDATE_PUB,
            PublishStatus.SUCCESS,
        ) or (
            rollback_status == PublishStatus.ONLINE_PUB
            and self.is_online_release_recorded(publish_id)
        )
        if restart:
            # BaaS publish failed; re-deploy via the restart work. Run
            # execute_restart INLINE (not the durable RESTART_TASK enqueue that the
            # /restart endpoint uses): the #162 poll enqueued below and the restart
            # task would otherwise be claimed in the same worker batch and run
            # concurrently, so the poll could read ext.restart before the task wrote
            # it. Running inline writes ext.restart + re-delivers synchronously, so
            # the poll then self-drives the record out of its *_PUB wait state. The
            # runner inside execute_restart still gives crash-safe, idempotent
            # issuance (adopt-by-query on a user re-retry).
            stage = self._determine_restart_stage(rollback_status)
            try:
                restart_result = await self.execute_restart(
                    publish_id=publish_id,
                    stage=stage.value,
                    operator=operator,
                )
            except Exception as e:
                logger.warning(
                    "[PublishFlowService.retry] execute_restart failed: "
                    "publish_id=%s stage=%s error=%s",
                    publish_id, stage.value if stage else None, e,
                )
                restart_result = {"success": False, "message": str(e)}
            success = restart_result.get("success", False)
            if success:
                # (#162) The BaaS-restart branch parks the record in its *_PUB wait
                # state without passing through verify_flow/online_release, so it
                # advanced only via user /sync polling (retry redirect) or an explicit
                # /restart_status poll. Enqueue the durable poll so the retried
                # restart self-drives: the poll's retry-flag redirect routes it
                # through sync_restart_progress and leaves the *_PUB state.
                enqueue_progress_poll(self._task_queue_service, publish_id=publish_id)
            else:
                self._mutate_and_update_ext(
                    publish_id=publish_id,
                    mutator=self._clear_retry_flag,
                )
            return PublishFlowResult(
                publish_id=publish_id,
                status=rollback_status,
                action="restart",
                message="Retry submitted (BaaS restart)" if success else f"Retry failed: {restart_result.get('message', 'Unknown error')}",
            )
        elif rollback_status in (PublishStatus.VALIDATING, PublishStatus.ONLINE_PUB):
            # Online release retry: re-open ONLINE_PUB (idempotent if already there)
            # and re-enqueue the online_release task, which re-runs the release work.
            self._advance_status(
                publish_id, PublishStatus.ONLINE_PUB, PublishStatus.VALIDATING
            )
            enqueue_online_release(
                self._task_queue_service, publish_id=publish_id, operator=operator
            )
        else:
            # BUILDING / BUILT: re-enqueue the verify_flow task (the build sub-step
            # is skipped when already BUILT).
            if rollback_status == PublishStatus.BUILDING:
                # A rebuild changes the artifact, so any release op from the failed
                # attempt is superseded — abandon it (#197 abandonment) so the fresh
                # attempt opens new ledger ops rather than resuming/adopting a stale
                # workflow built from the old artifact.
                self._abandon_inflight_operations(
                    publish_id, reason="retry rebuild — superseded"
                )
            enqueue_verify_flow(
                self._task_queue_service, publish_id=publish_id, operator=operator
            )
        return PublishFlowResult(
            publish_id=publish_id,
            status=rollback_status,
            action="process",
            message="Retry submitted, please check progress later",
        )
