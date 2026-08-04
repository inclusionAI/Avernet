"""Bot restart (re-deploy) operations, mixed into PublishFlowService."""
from __future__ import annotations

from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationKind,
    PublishOperationState,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.deploy.service_skills_manifest import (
    service_skills_env_from_ext,
)
from agentclaw.community.core.service_bot.services.arka_image_pin import (
    resolve_publish_image_pin,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.services.publish_flow.operation_runner import (
    TargetBotGoneError,
    acquire_deploy_workflow,
)
from agentclaw.community.core.service_bot.types import (
    OnlineDeployDecision,
    PublishStage,
)
from agentclaw.community.log import get_logger

logger = get_logger()

DEVICE_COUNT_CONFIG_BUSINESS_CODE = "service_bot_device_count"
DEVICE_COUNT_DEFAULT_PARAM_CODE = "default"


class RestartMixin:
    """Bot restart (re-deploy) operations, mixed into PublishFlowService."""

    # ── durable-task accessors ───────────────────────────────────────────────
    # The restart poll and the restart task read the record through these rather
    # than reaching into the private collaborators. They live on the mixin with
    # the rest of the restart concern, and are deliberately NOT on
    # ``PublishFlowServiceProtocol`` — that carries the API-boundary methods
    # adapters call, not task-handler accessors.
    def is_restart_in_progress(self, publish_id: int) -> bool:
        """True while a restart issued for this record is still awaiting its BaaS
        workflow — ``ext.restart.restarting`` is set *and* the record is in a
        status ``sync_restart_progress`` can still resolve a stage for.

        The restart poll's wait-state test, standing in for the publish status
        the restart path never transitions. The status half matters as much as
        the marker: once a record leaves the restartable statuses the sync can
        never reconcile it either (it derives the stage from the status too), so
        the poll must stop rather than spin to its deadline."""
        record = self._publish_service.get_publish_by_id(publish_id)
        if record is None:
            return False
        if self._determine_restart_stage(PublishStatus(record.status)) is None:
            return False
        restart_ext = (record.ext or {}).get("restart")
        return isinstance(restart_ext, dict) and bool(restart_ext.get("restarting"))

    def has_unreconciled_restart(self, publish_id: int, stage: str) -> bool:
        """True when a restart for ``stage`` was already submitted and is still
        awaiting its terminal workflow.

        The restart task's redelivery guard. ``execute_restart`` COMPLETEs its
        ledger op before returning, and ``open_publish_operation`` resumes only a
        *non-terminal* op — past a terminal one it opens the next attempt. So an
        at-least-once redelivery would issue a second BaaS restart unless the
        handler can recognise a restart it already submitted.

        All three conditions are required, and the third is the subtle one: a
        crash mid-``execute_restart`` (its RESTART op still open) or between a
        recreate's ext write and its completion (a FIRST_RELEASE op still open)
        must fall *through* this guard, so the runner resumes and finalises that
        work exactly as it does today. Only a stage with no deploy operation left
        open is genuinely "submitted, nothing left to run but the observation"."""
        record = self._publish_service.get_publish_by_id(publish_id)
        if record is None:
            return False

        restart_ext = (record.ext or {}).get("restart")
        if not isinstance(restart_ext, dict):
            return False
        if not restart_ext.get("restarting") or not restart_ext.get(stage):
            return False

        terminal = {state.value for state in PublishOperationState.terminal()}
        for kind in (PublishOperationKind.RESTART, PublishOperationKind.FIRST_RELEASE):
            op = self._publish_operation_repo.get_latest_by_kind(
                publish_id, kind.value, stage
            )
            if op is not None and op.state not in terminal:
                return False
        return True

    def restart_bot(
        self,
        publish_id: int,
        operator: str = "system",
    ) -> dict:
        """Submit a Bot restart (durable, crash-safe).

        Determines the current stage from the publish record status, validates the
        binding/bot/artifact, and enqueues the durable restart task (#197). The
        actual re-deploy runs in ``execute_restart`` through the operation runner —
        this method returns as soon as the task is submitted (it does not wait for
        the re-deploy). Replaces the former fire-and-forget ``asyncio.create_task``.

        Flow:
        1. Resolve + validate the target (record → stage → binding → bot → artifact)
        2. Enqueue the durable restart task

        Args:
            publish_id: Publish record ID
            operator: Operator, defaults to "system"

        Returns:
            dict: Restart result, containing:
                - success: Whether the restart request was submitted successfully
                - message: Result message
                - stage: Publish stage (returned on success)
        """
        logger.info(
            f"[PublishFlowService.restart_bot] called: publish_id={publish_id}, operator={operator}"
        )

        # Resolve + validate the restart target (record → stage → binding → bot →
        # artifact). On any failure ``error`` is the response dict to return.
        error, stage, bot_uuid = self._resolve_restart_request(publish_id)
        if error is not None:
            return error

        # (#197) Enqueue the DURABLE restart task instead of a fire-and-forget
        # asyncio task. The handler re-resolves and runs the re-deploy through the
        # operation runner (crash-safe, idempotent).
        from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
            enqueue_restart,
        )

        enqueue_restart(
            self._task_queue_service,
            publish_id=publish_id,
            stage=stage.value,
            operator=operator,
        )

        logger.info(
            f"[PublishFlowService.restart_bot] Restart task enqueued: "
            f"publish_id={publish_id}, bot_uuid={bot_uuid}, stage={stage.value}, "
            f"operator={operator}"
        )

        return {
            "success": True,
            "message": f"Restart task submitted, stage: {stage.value}",
            "stage": stage.value,
            "bot_uuid": bot_uuid,
        }

    def _resolve_restart_request(self, publish_id: int):
        """Validate + resolve a restart request from ``publish_id``.

        Returns ``(error, stage, bot_uuid)``: on failure ``error`` is the response
        dict (``success=False`` + message) and stage/bot_uuid are None; on success
        ``error`` is None and stage/bot_uuid are populated. The build-artifact
        presence is also checked here so the durable handler always has one."""
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            logger.warning(
                f"[PublishFlowService.restart_bot] Publish record not found: publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"Publish record not found: publish_id={publish_id}",
            }, None, None

        current_status = PublishStatus(publish_record.status)
        stage = self._determine_restart_stage(current_status)
        if not stage:
            logger.warning(
                f"[PublishFlowService.restart_bot] "
                f"Cannot restart for status: {current_status}, publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"Current status {current_status} does not support restart operation",
                "status": current_status,
            }, None, None

        # (#197) The previous ext.restart marker is NO LONGER cleared here.
        # Clearing it before the new workflow id was recorded was a crash hazard
        # (a crash after clear left no marker at all). The durable restart op now
        # owns idempotency; the marker is dual-written by ``execute_restart`` once
        # the new workflow id is recorded.
        ext = self._get_latest_ext(publish_id)

        binding_id = (ext.get("binding", {}) or {}).get(stage.value)
        if not binding_id:
            logger.warning(
                f"[PublishFlowService.restart_bot] "
                f"No binding_id found for stage={stage.value}, publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"No binding info found for stage {stage.value}",
                "stage": stage.value,
            }, None, None

        binding = self._publish_service.get_device_binding_by_id(binding_id)
        if not binding:
            logger.warning(
                f"[PublishFlowService.restart_bot] Device binding not found: binding_id={binding_id}"
            )
            return {
                "success": False,
                "message": f"Device binding record not found: binding_id={binding_id}",
            }, None, None

        bot_uuid = binding.device_id
        if not bot_uuid:
            logger.warning(
                f"[PublishFlowService.restart_bot] No device_id in binding: binding_id={binding_id}"
            )
            return {
                "success": False,
                "message": f"Device binding record has no device_id: binding_id={binding_id}",
            }, None, None

        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id, user_id=publish_record.owner_id
        )
        if not bot:
            logger.warning(
                f"[PublishFlowService.restart_bot] Bot not found: bot_id={publish_record.source_bot_id}"
            )
            return {
                "success": False,
                "message": f"Bot not found: {publish_record.source_bot_id}",
            }, None, None

        if not ext.get("migration_path") and not ext.get("config_artifact"):
            logger.warning(
                f"[PublishFlowService.restart_bot] No build artifact in ext: publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"Publish record is missing build artifact: publish_id={publish_id}",
            }, None, None

        return None, stage, bot_uuid

    async def execute_restart(
        self,
        publish_id: int,
        stage: str,
        operator: str,
    ) -> dict:
        """Durable Bot restart work (#197): re-deploy the existing bot via the
        upgrade interface, through the operation runner so a crash-resume adopts
        the in-doubt restart workflow instead of issuing a second one.

        Re-resolves everything from ``publish_id`` (the durable task carries only
        ids), then runs the deploy atom for a ``restart`` op keyed to the bot
        (upgrade interface; a BOT_NOT_FOUND abandons the op and hands off to
        :meth:`_recreate_restart_target` — a fresh, crash-safe first-release op
        with a new bot + binding), dual-writes ``ext.restart.<stage>`` for the
        status read, refreshes the teclaw read handle, and completes the op.
        Returns ``{success, message}``."""
        stage_enum = PublishStage(stage)
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            return {"success": False, "message": f"Publish record not found: {publish_id}"}

        ext = self._get_latest_ext(publish_id)
        binding_id = (ext.get("binding") or {}).get(stage_enum.value)
        if not binding_id:
            return {"success": False, "message": f"No binding for stage {stage_enum.value}"}
        binding = self._publish_service.get_device_binding_by_id(binding_id)
        if not binding or not binding.device_id:
            return {"success": False, "message": f"Device binding missing device_id: {binding_id}"}
        bot_uuid = binding.device_id

        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id, user_id=publish_record.owner_id
        )
        if not bot:
            return {"success": False, "message": f"Bot not found: {publish_record.source_bot_id}"}

        migration_path = ext.get("migration_path")
        config_artifact = ext.get("config_artifact")
        if not migration_path and not config_artifact:
            return {"success": False, "message": f"Missing build artifact: publish_id={publish_id}"}

        # Mark restart in progress, and note that every failure return above this
        # point leaves it untouched — the restart task's failure branch relies on
        # that to avoid clearing a concurrent restart's marker. Cleared on an
        # observed terminal workflow by sync_restart_progress, which the restart
        # poll now drives.
        def _set_restarting_flag(latest_ext: dict) -> None:
            latest_ext.setdefault("restart", {})["restarting"] = True

        self._mutate_and_update_ext(publish_id=publish_id, mutator=_set_restarting_flag)

        version = f"{publish_record.version}"
        # STORED overrides slot: reproduce what was promoted (not a live re-fetch),
        # so restarting a non-latest stage never delivers another stage's channels.
        delivery = self._ext_state.compose_stored(publish_record.ext or {}, stage_enum)
        skills_env = service_skills_env_from_ext(publish_record.ext, bot)
        image_pin = resolve_publish_image_pin(publish_record)

        # A prior recreate that crashed between its ext write and its
        # complete_operation left a dangling op. That crashed leg IS this restart
        # request's deploy (ext.restart/publish.<stage> already point at its
        # workflow), and RESTART_TASK is at-least-once — so a redelivery must
        # finish the recreate's bookkeeping and return its existing workflow, NOT
        # open a second RESTART op and issue another deploy (which would leave two
        # concurrent deploy workflows for one restart). Short-circuit when found.
        recreate_wid = self._finalize_dangling_recreate_op(publish_id, stage_enum, ext)
        if recreate_wid is not None:
            self.refresh_publish_handle(binding_id, recreate_wid)
            logger.info(
                "[PublishFlowService.execute_restart] resumed crashed recreate "
                "(idempotent redelivery): publish_id=%s stage=%s restart_publish_id=%s",
                publish_id, stage_enum.value, recreate_wid,
            )
            return {"success": True,
                    "message": f"Restart submitted, stage: {stage_enum.value}",
                    "stage": stage_enum.value, "restart_publish_id": recreate_wid}

        # Provider-aware reuse-vs-recreate decision (online stage). Any non-UPGRADE
        # decision recreates *directly* rather than issuing the in-place UPGRADE:
        # a teclaw not-live target's UPDATE cannot rebuild a gone container (it
        # would silently fail the publish), and a DESTROYING/gone target's UPDATE
        # is rejected with an error the atom does not classify as BOT_NOT_FOUND —
        # both would strand the restart. Before recreating we open+abandon a fresh
        # RESTART op so ``sync_restart_progress`` (which prefers the latest RESTART
        # op's workflow id) does not read a stale earlier restart, and instead
        # falls back to the ``ext.restart`` handle the recreate writes.
        if stage_enum == PublishStage.ONLINE:
            decision = self._decide_online_deploy(publish_record, bot)
            if decision not in (
                OnlineDeployDecision.UPGRADE,
                OnlineDeployDecision.RETIRE_THEN_FIRST_RELEASE,
                OnlineDeployDecision.FIRST_RELEASE,
            ):
                raise PublishFlowServiceError(
                    f"Unhandled online deploy decision: {decision}"
                )
            if decision != OnlineDeployDecision.UPGRADE:
                # Release the record's now-stale online binding before recreating
                # (the recreate below mints a fresh one and rewrites
                # ext.binding.<stage>), so the old binding does not linger ACTIVE
                # pointing at a gone/retired bot. For RETIRE_THEN_FIRST_RELEASE we
                # destroy the still-registered bot first and stash its destroy id;
                # for FIRST_RELEASE the target is already gone (RELEASED/DESTROYING,
                # e.g. an external BaaS deletion) so no destroy is issued.
                destroy_publish_id = None
                if decision == OnlineDeployDecision.RETIRE_THEN_FIRST_RELEASE:
                    destroy_publish_id = self._build_service.retire_superseded_bot(
                        bot_uuid, operator=operator
                    )
                self._release_binding(
                    binding_id, destroy_publish_id=destroy_publish_id
                )
                # Supersede any prior RESTART op with a fresh abandoned one, so
                # restart-status reads the recreate's workflow (via ext.restart).
                superseding_op = self._operation_runner.open_operation(
                    publish_id=publish_id,
                    kind=PublishOperationKind.RESTART,
                    stage=stage_enum,
                    bot_uuid=bot_uuid,
                    operator=operator,
                )
                self._operation_runner.abandon_operation(
                    superseding_op, f"{decision.value} -> recreate"
                )
                logger.info(
                    "[PublishFlowService.execute_restart] decision=%s -> recreate: "
                    "publish_id=%s bot_uuid=%s stage=%s",
                    decision.value, publish_id, bot_uuid, stage_enum.value,
                )
                return await self._recreate_restart_target(
                    publish_id=publish_id,
                    stage_enum=stage_enum,
                    publish_record=publish_record,
                    bot=bot,
                    migration_path=migration_path,
                    version=version,
                    delivery=delivery,
                    skills_env=skills_env,
                    docker_image=image_pin.docker_image,
                    operator=operator,
                )

        async def _issue():
            return await self._build_service.upgrade_async(
                bot_uuid=bot_uuid,
                bot=bot,
                user_id=publish_record.owner_id,
                device_count=1,
                migration_path=migration_path,
                publish_stage=stage_enum,
                version=version,
                delivery=delivery,
                extra_envs=skills_env,
                docker_image=image_pin.docker_image,
            )
        # NOTE: transient errors out of the atom are NOT caught + failed here. A
        # genuine crash leaves the op non-terminal so the durable task retry
        # re-runs and the SAME op resumes → adopt-by-query (existing bot).
        # Catching + fail_operation would open a fresh attempt on retry and
        # re-issue a second restart. The one classified signal is BOT_NOT_FOUND:
        # the atom abandons the RESTART op and the recreate leg runs as its own
        # fresh, crash-safe first-release op (new bot + NEW binding) — the same
        # guarantee upgrade_release's fallback has, replacing the former inline
        # recreate whose crash window could orphan a second bot.
        try:
            op = await acquire_deploy_workflow(
                self._operation_runner,
                publish_id=publish_id,
                kind=PublishOperationKind.RESTART,
                stage=stage_enum,
                operator=operator,
                issue=_issue,
                bot_uuid=bot_uuid,
                bot_gone_reason="BOT_NOT_FOUND -> recreate",
            )
        except TargetBotGoneError as e:
            logger.warning(
                "[PublishFlowService.execute_restart] target bot gone (%s), "
                "recreating via first release: publish_id=%s bot_uuid=%s stage=%s",
                e.error_code, publish_id, bot_uuid, stage_enum.value,
            )
            # Secondary net (mirrors upgrade_release). Either way the recreate
            # below mints a fresh binding and rewrites ext.binding.<stage>, so the
            # record's current binding must be released or it lingers ACTIVE
            # pointing at the gone bot. A DEVICE_NOT_FOUND means the record still
            # lingers with a gone container, so retire it first (and stash the
            # destroy id); a BOT_NOT_FOUND means the bot is already fully gone (no
            # destroy needed → destroy_publish_id=None).
            destroy_publish_id = None
            if e.error_code == "DEVICE_NOT_FOUND":
                destroy_publish_id = self._build_service.retire_superseded_bot(
                    bot_uuid, operator=operator
                )
            self._release_binding(
                binding_id, destroy_publish_id=destroy_publish_id
            )
            return await self._recreate_restart_target(
                publish_id=publish_id,
                stage_enum=stage_enum,
                publish_record=publish_record,
                bot=bot,
                migration_path=migration_path,
                version=version,
                delivery=delivery,
                skills_env=skills_env,
                docker_image=image_pin.docker_image,
                operator=operator,
            )
        restart_publish_id = op.baas_publish_id

        # Refresh the teclaw read handle to the restart workflow (best-effort).
        self.refresh_publish_handle(binding_id, restart_publish_id)

        # Dual-write ext.restart.<stage> for sync_restart_progress / #157 (the
        # ledger op is the source of truth; ext is the read handle).
        try:
            def _mutate(latest_ext: dict) -> None:
                latest_ext.setdefault("restart", {})[stage_enum.value] = restart_publish_id

            self._mutate_and_update_ext(publish_id=publish_id, mutator=_mutate)
        except Exception as save_error:
            logger.warning(
                "[PublishFlowService.execute_restart] failed to save ext.restart: "
                "publish_id=%s error=%s", publish_id, save_error,
            )

        self._operation_runner.complete_operation(op)
        logger.info(
            "[PublishFlowService.execute_restart] restart submitted: bot_uuid=%s "
            "stage=%s restart_publish_id=%s", bot_uuid, stage_enum.value, restart_publish_id,
        )
        return {"success": True, "message": f"Restart submitted, stage: {stage_enum.value}",
                "stage": stage_enum.value, "restart_publish_id": restart_publish_id}
    def _finalize_dangling_recreate_op(
        self, publish_id: int, stage_enum: PublishStage, ext: dict
    ) -> int | None:
        """Complete a recreate FIRST_RELEASE op stranded by a crash between its
        ext write and its ``complete_operation``; return its workflow id (or
        ``None`` when there is nothing to finalize).

        The recreate's last two steps are the ext write (binding/publish/restart
        → the new ids) and the op completion. A crash between them leaves the op
        ``ID_RECORDED`` while ext already points at the NEW, alive bot — so the
        re-delivered restart resolves that bot and would otherwise take the happy
        (upgrade) path, and the recreate leg never runs again to finish its
        bookkeeping. Left dangling, that non-terminal latest FIRST_RELEASE attempt
        hides the record's genuinely completed release from
        ``is_current_online_deployment`` (which reads the latest attempt per
        kind), and a later release for the same record/stage would resume it and
        complete without issuing anything.

        The condition is exact: a non-terminal FIRST_RELEASE op for this
        record/stage whose recorded workflow id IS **both** ``ext.publish.<stage>``
        and ``ext.restart.<stage>``. Requiring the ``restart`` match too is what
        keeps this specific to a restart-recreate redelivery: ``_recreate_restart_target``
        writes the same workflow id into publish AND restart, whereas a *normal*
        online first release (which can also crash after ``record_release_ext``
        writes ``ext.publish.online`` but before ``complete_operation``, and whose
        stage is restartable) never touches ``ext.restart`` — so without this
        clause a restart issued in that window would finalize the release op and
        return without actually restarting. That release op is finalized by the
        online_release task's own redelivery, not here.

        ``complete`` is a CAS from ID_RECORDED; anything else no-ops. The returned
        id lets ``execute_restart`` return that workflow instead of issuing a
        redundant second deploy (idempotent task redelivery)."""
        op = self._publish_operation_repo.get_latest_by_kind(
            publish_id, str(PublishOperationKind.FIRST_RELEASE), stage_enum.value
        )
        if op is None or op.baas_publish_id is None:
            return None
        terminal = {s.value for s in PublishOperationState.terminal()}
        if op.state in terminal:
            return None
        if op.baas_publish_id != (ext.get("publish") or {}).get(stage_enum.value):
            return None
        if op.baas_publish_id != (ext.get("restart") or {}).get(stage_enum.value):
            return None
        logger.info(
            "[PublishFlowService._finalize_dangling_recreate_op] completing "
            "stranded recreate op: publish_id=%s stage=%s op_id=%s baas_publish_id=%s",
            publish_id, stage_enum.value, op.id, op.baas_publish_id,
        )
        self._operation_runner.complete_operation(op)
        return op.baas_publish_id

    async def _recreate_restart_target(
        self,
        *,
        publish_id: int,
        stage_enum: PublishStage,
        publish_record,
        bot: dict,
        migration_path: str | None,
        version: str,
        delivery,
        skills_env: dict[str, str] | None,
        docker_image: str | None,
        operator: str,
    ) -> dict:
        """Recreate a restart's gone target bot — crash-safe (closes the former
        known limitation).

        Runs AFTER the RESTART op was abandoned (``BOT_NOT_FOUND -> recreate``)
        as its own ``FIRST_RELEASE`` op: a creation kind, so a crash-resume
        rebuilds from the ledger exactly like a normal first release (issue-once
        with the bounded Option-C orphan window, follow-ups skipped via the op
        ``result``) — never adopt-by-query against the old, gone bot. The
        recreate genuinely deploys this record's version as a fresh bot, so
        ``FIRST_RELEASE`` is also the semantically right kind: for an online
        recreate the record's latest release op is this deploy, and the
        liveness gate correctly reads it as the current deployment.

        A **new** binding is minted (recorded into the op ``result`` so a
        re-run reuses it) — the old binding still points at the gone bot_uuid
        and is never reused. Ext gets ``binding/publish/restart.<stage>`` in
        one write: binding/publish are the release read handles, and
        ``restart.<stage>`` keeps ``sync_restart_progress`` working — after the
        RESTART op's abandonment the ledger holds no restart workflow id, so
        the sync falls back to that ext marker. The record's status is not
        touched (a restart runs at SUCCESS / *_PUB; there is no transition
        here)."""
        async def _issue():
            return await self._build_service.release_async(
                bot=bot,
                user_id=publish_record.owner_id,
                migration_path=migration_path,
                device_count=1,
                publish_stage=stage_enum,
                version=version,
                delivery=delivery,
                extra_envs=skills_env,
                docker_image=docker_image,
            )

        op = await acquire_deploy_workflow(
            self._operation_runner,
            publish_id=publish_id,
            kind=PublishOperationKind.FIRST_RELEASE,
            stage=stage_enum,
            operator=operator,
            issue=_issue,
        )
        new_bot_uuid = op.bot_uuid
        recreate_publish_id = op.baas_publish_id

        binding_id = (op.result or {}).get("binding_id")
        if binding_id is None:
            binding_id = self.create_release_binding(
                bot=bot,
                bot_uuid=new_bot_uuid,
                baas_publish_id=recreate_publish_id,
                operator=operator,
            )
            op = self._operation_runner.record_step_result(op, {"binding_id": binding_id})

        def _mutate(latest_ext: dict) -> None:
            latest_ext.setdefault("binding", {})[stage_enum.value] = binding_id
            latest_ext.setdefault("publish", {})[stage_enum.value] = recreate_publish_id
            latest_ext.setdefault("restart", {})[stage_enum.value] = recreate_publish_id

        self._mutate_and_update_ext(publish_id=publish_id, mutator=_mutate)

        self.refresh_publish_handle(binding_id, recreate_publish_id)
        self._operation_runner.complete_operation(op)
        logger.info(
            "[PublishFlowService._recreate_restart_target] recreated: publish_id=%s "
            "stage=%s new_bot_uuid=%s recreate_publish_id=%s binding_id=%s",
            publish_id, stage_enum.value, new_bot_uuid, recreate_publish_id, binding_id,
        )
        return {"success": True,
                "message": f"Restart target recreated, stage: {stage_enum.value}",
                "stage": stage_enum.value, "restart_publish_id": recreate_publish_id}
