"""Ordinary coding BaaS restart orchestration; never called by other engines.

Own the backup -> prepare -> submit boundary rather than inserting callbacks
into BotService or the BaaS client. Published/Caller and direct-provider paths
continue to use their existing entrypoints. All shared services are reused as
collaborators, never monkey-patched or reconfigured per request.
"""

from __future__ import annotations

import time

from agentclaw.community.core.devices.models import DeviceBindingStatus

from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

from .restart_state import RestartSuperseded, current_restart, journal

logger = get_logger()


def _field(record, name, default=None):
    return (
        record.get(name, default)
        if isinstance(record, dict)
        else getattr(record, name, default)
    )


class RestartSubmissionRejected(RuntimeError):
    """The provider explicitly rejected the request; do not await a publish."""


def rejection_status(error: Exception) -> int | None:
    """Walk wrappers without persisting transport bodies, URLs or credentials.

    Timeouts, conflict/idempotency responses and server errors are deliberately
    NOT definitive: they may follow an accepted provider mutation.
    """
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int) and status in {400, 401, 403, 404, 422}:
            return status
        error = error.__cause__ or error.__context__
    return None


def find_rejection(error: Exception) -> RestartSubmissionRejected | None:
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, RestartSubmissionRejected):
            return error
        error = error.__cause__ or error.__context__
    return None


class AicodingSubmissionMixin:
    def before_restart_submission(self, ctx) -> None:
        execution = current_restart.get()
        if execution is None or execution.payload["provider"] != "baas":
            return
        execution.check_target(ctx, execution.payload["binding_id"])
        if execution.verify_backup is None:
            raise RuntimeError("缺少已验证的备份，禁止提交重启")
        # Configuration preparation can take time. Recheck the actual container
        # and committed generation at the last possible moment before submission.
        execution.verify_backup()
        execution.fence_mutation()

    def on_restart_submission_error(self, ctx, error, *, clear_intent) -> None:
        execution = current_restart.get()
        if execution is None or execution.payload["provider"] != "baas":
            return
        status = rejection_status(error) if execution.fenced else None
        if execution.fenced and status is None:
            return
        bot = execution.state.read()
        record = journal(bot)
        expected_phase = "RESTARTING" if execution.fenced else "BACKING_UP"
        if (
            record.get("operation_id") != execution.operation_id
            or record.get("phase") != expected_phase
            or bot.get("binding_id") != execution.payload["binding_id"]
        ):
            raise RestartSuperseded("Restart submission ownership changed")
        if not execution.fenced:
            # Includes payload construction and the final receipt check. The
            # coding executor's submission fence has not authorized POST yet.
            clear_intent()
            return
        # The old provider poller must not adopt an unrelated future publish.
        # A persistence failure propagates: do not pretend cleanup succeeded.
        clear_intent()
        raise RestartSubmissionRejected(
            f"BaaS 拒绝重启请求（HTTP {status}），本次重启失败；旧容器未执行替换"
        ) from error


class AicodingBaasRestart:
    def __init__(self, service):
        from .restart_request import AicodingBaasPreparation

        self.service = service
        self.preparation = AicodingBaasPreparation(service)

    @staticmethod
    def _validate_binding(bot):
        from ...services.bot_service import BotInvalidLifecycleStateError

        status = (bot.get("device_binding") or {}).get("status")
        if status and status not in {"ACTIVE", "PENDING", "FAILED", "STOPPED"}:
            raise BotInvalidLifecycleStateError(
                bot_id=bot["bot_id"],
                current_status=f"BINDING_{status}",
            )

    def preflight(self, bot, owner_id):
        from .restart_request import BaasRestartInProgress

        self._validate_binding(bot)
        prepared = self.preparation.prepare(
            bot_id=bot["bot_id"],
            user_id=owner_id,
            binding_id=bot["binding_id"],
            bot=bot,
        )
        return prepared.bot if isinstance(prepared, BaasRestartInProgress) else None

    def _check_current_binding(self, execution):
        from .restart_backup import RestartBackupError

        binding = self.service._device_service_provider().get_device(
            binding_id=execution.payload["binding_id"],
        )
        if (
            _field(binding, "device_id") != execution.payload["device_id"]
            or _field(binding, "device_provider") != "baas"
        ):
            raise RestartBackupError("target_changed", "当前设备绑定已变化，禁止替换")
        return binding

    def execute(self, bot, execution):
        from .restart_request import BaasRestartInProgress

        from ..registry import resolve_restart_strategy

        service = self.service
        state = execution.state
        self._validate_binding(bot)
        ctx, strategy = resolve_restart_strategy(bot)
        execution.check_target(ctx, bot["binding_id"])
        self._check_current_binding(execution)
        verify = strategy.prepare_restart(
            ctx,
            binding_id=bot["binding_id"],
            device_service_provider=service._device_service_provider,
            target_runtime_provider=service._baas_service_provider,
            bot_repository=service._repository,
        )
        env, entity_id = get_current_env(), bot["entity_id"]
        lock = service._try_acquire_restart_lock(
            env, entity_id, state.bot_id, state.owner_id
        )
        if lock is None:
            return state.read()  # no fence, worker reschedules without submitting
        try:
            verify()
            execution.check_target(ctx, bot["binding_id"])
            if bot.get("bot_type") == "service":
                bot = service._mark_service_bot_default_image(bot)
            prepared = self.preparation.prepare(
                bot_id=state.bot_id,
                user_id=state.owner_id,
                binding_id=bot["binding_id"],
                bot=bot,
            )
            if isinstance(prepared, BaasRestartInProgress):
                return prepared.bot
            if prepared.bot_uuid != execution.payload["device_id"]:
                from .restart_backup import RestartBackupError

                raise RestartBackupError("target_changed", "提交目标已变化，禁止替换")
            # All local request construction has completed. Persist observer
            # intent before POST, but do not mark mutation as submitted yet.
            return self._submit(prepared, ctx, strategy, execution)
        finally:
            service._restart_lock_repo.release(
                env, entity_id, state.bot_id, lock.lock_token
            )

    def _clear_owned_intent(self, prepared, execution):
        from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
            RESTART_IMAGE_POLICY_ON_SUCCESS_KEY,
            RESTART_REQUEST_ID_KEY,
            RESTART_WORKFLOW_BASELINE_KEY,
        )
        service = self.service
        binding_id = execution.payload["binding_id"]
        binding = service._device_service_provider().get_device(binding_id=binding_id)
        props = _field(binding, "device_props", {}) or {}
        if props.get(RESTART_REQUEST_ID_KEY) != prepared.request_id:
            return
        service._device_binding_repo.update_device_props(
            binding_id=binding_id,
            props={
                RESTART_REQUEST_ID_KEY: None,
                RESTART_WORKFLOW_BASELINE_KEY: None,
                RESTART_IMAGE_POLICY_ON_SUCCESS_KEY: None,
            },
        )

    def _submit(self, prepared, ctx, strategy, execution):
        from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
            BAAS_RESTART_PUBLISH_POLL_TASK,
            RESTART_IMAGE_POLICY_ON_SUCCESS_KEY,
            RESTART_REQUEST_ID_KEY,
            RESTART_WORKFLOW_BASELINE_KEY,
            build_restart_publish_poll_payload,
        )
        service, state = self.service, execution.state
        binding_id = execution.payload["binding_id"]
        try:
            service._task_queue_service.enqueue(
                BAAS_RESTART_PUBLISH_POLL_TASK,
                build_restart_publish_poll_payload(
                    binding_id=binding_id,
                    bot_id=state.bot_id,
                    owner_id=state.owner_id,
                    publish_id=None,
                    started_at_epoch_s=time.time(),
                    bot_uuid=prepared.bot_uuid,
                    image_policy_on_success=prepared.image_policy_on_success,
                    request_id=prepared.request_id,
                    workflow_baseline=prepared.workflow_baseline,
                ),
                deadline_seconds=86400,
                delay_seconds=2,
            )
            execution.check_target(ctx, binding_id)
            service._device_binding_repo.update_device_props(
                binding_id=binding_id,
                props={
                    RESTART_REQUEST_ID_KEY: prepared.request_id,
                    RESTART_WORKFLOW_BASELINE_KEY: prepared.workflow_baseline,
                    "restart_publish_id": None,
                    RESTART_IMAGE_POLICY_ON_SUCCESS_KEY: prepared.image_policy_on_success,
                },
            )
            service._device_binding_repo.update_status(
                binding_id=binding_id,
                status=DeviceBindingStatus.PENDING,
            )
            # Admission already wrote Bot.PENDING; never repeat that write here.
            # The receipt and physical target are checked at this exact boundary.
            self._check_current_binding(execution)
            strategy.before_restart_submission(ctx)
            result = service._baas_service_provider().post_bots_api(
                path=f"/api/v1/bots/{prepared.bot_uuid}/update",
                payload=prepared.request_payload,
                action="coding_restart",
            )
        except Exception as error:
            strategy.on_restart_submission_error(
                ctx,
                error,
                clear_intent=lambda: self._clear_owned_intent(prepared, execution),
            )
            raise
        publish_id = (
            (result or {}).get("publish_id") if isinstance(result, dict) else None
        )
        if publish_id is not None:
            try:
                service._device_binding_repo.update_device_props(
                    binding_id=binding_id,
                    props={
                        "publish_id": str(publish_id),
                        "restart_publish_id": str(publish_id),
                    },
                )
            except Exception:
                # Existing durable provider poller adopts this accepted request;
                # never replay POST because its local response write failed.
                logger.warning(
                    "coding restart publish persistence failed: bot_id=%s", state.bot_id
                )
        return state.read()
