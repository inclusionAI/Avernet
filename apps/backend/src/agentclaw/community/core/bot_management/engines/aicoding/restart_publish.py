"""Coding-owned published restart veto, replay and failure persistence.

The shared flow still owns provider mutation and workflow adoption. This policy
wraps those calls without replacing shared services or changing other engines.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from ..restart_contract import PublishedRestartServices
from .restart_backup import RestartBackupBlocked
from .restart_failure_log import log_restart_failure
from .restart_state import ENGINES


class AicodingPublishedRestartMixin:
    async def execute_published_restart(
        self, ctx, restart, *, services: PublishedRestartServices,
        publish_id: int, stage: str, binding_id: int, source_status: str,
    ) -> dict:
        if str(ctx.active_engine or "").strip().lower() not in ENGINES:
            return await restart()

        from agentclaw.community.core.service_bot.repository.models import PublishStatus

        record = services.get_record(publish_id)
        if record is None:
            raise RuntimeError(f"Publish record not found: {publish_id}")
        blocked = ((record.ext or {}).get("restart") or {}).get("precondition_error") or {}
        if record.status == PublishStatus.FAILED.value and blocked.get("stage") == stage:
            # A queue redelivery must not forget a terminal backup veto.
            return {"success": False, "message": blocked["message"]}

        guard_id = uuid4().hex

        def claim(ext: dict) -> None:
            state = ext.setdefault("restart", {})
            state["guard"] = {
                "id": guard_id, "binding_id": binding_id, "status": source_status,
            }
            state.pop("precondition_error", None)

        services.mutate_ext(publish_id=publish_id, mutator=claim)
        try:
            return await restart()
        except RestartBackupBlocked as error:
            log_restart_failure(error, bot_id=ctx.bot_id, phase="published_restart", owner_id=ctx.owner_id,
                                publish_id=publish_id, binding_id=binding_id)
            self._persist_published_restart_veto(
                services, publish_id=publish_id, stage=stage,
                guard_id=guard_id, message=str(error),
            )
            return {"success": False, "message": str(error)}

    @staticmethod
    def _persist_published_restart_veto(
        services: PublishedRestartServices, *, publish_id: int,
        stage: str, guard_id: str, message: str,
    ) -> None:
        # Import lazily: service_bot package initialization consumes the engine registry.
        from agentclaw.community.core.service_bot.repository.models import PublishStatus

        record = services.get_record(publish_id)
        if record is None:
            raise RuntimeError(f"Publish record not found: {publish_id}")
        expected_ext = deepcopy(record.ext)
        ext = deepcopy(record.ext or {})
        restart = ext.get("restart") or {}
        guard = restart.get("guard") or {}
        if (guard.get("id") != guard_id
                or guard.get("status") != record.status
                or (ext.get("binding") or {}).get(stage) != guard.get("binding_id")):
            return  # A concurrent restart/release owns the state now.
        restart.pop("restarting", None)
        restart.pop("guard", None)
        restart["precondition_error"] = {"stage": stage, "message": message}
        ext.pop("retry", None)
        ext["error_message"] = message
        ext["source_status"] = record.status
        # Persistence/CAS errors propagate: never claim the failure was saved.
        services.update_status(
            publish_id=publish_id, target_status=PublishStatus.FAILED,
            source_status=PublishStatus(record.status), ext=ext,
            expected_ext=expected_ext,
        )

    async def acquire_restart_workflow(self, ctx, op, issue, *, runner):
        if str(ctx.active_engine or "").strip().lower() not in ENGINES:
            return await runner.acquire_workflow(op, issue)

        async def guarded_issue():
            try:
                return await issue()
            except RestartBackupBlocked as error:
                # Close the exact op we were given, never a queried latest op.
                # No workflow was issued. Transient/post-submit errors are not
                # classified here and retain the runner's adoption semantics.
                runner.fail_operation(op, str(error))
                raise

        return await runner.acquire_workflow(op, guarded_issue)
