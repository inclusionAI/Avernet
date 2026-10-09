"""Durable ordinary-Bot restart policy for aicoding/claude_code only.

Published restart and Caller recovery continue to use prepare_restart_async;
this module is entered only through the ordinary HTTP execute_restart hook.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Callable

from agentclaw.community.core.bot_management.bot_service_protocol import (
    BotServiceProtocol,
)
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.task_queue.types import (
    Complete,
    Fail,
    Reschedule,
    TERMINAL_STATUSES,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)

from .restart_state import (
    BUSINESS_TIMEOUT,
    ENGINES,
    IN_PROGRESS,
    TASK_DEADLINE,
    TASK_TYPE,
    TERMINAL,
    RestartExecution,
    RestartState,
    RestartSuperseded,
    current_restart,
    journal,
    supports,
    task_key,
)

logger = get_logger()
POLL_SECONDS = 3


def _state(repository, payload) -> RestartState:
    return RestartState(
        repository, payload["bot_id"], payload["owner_id"], payload["operation_id"]
    )


def _failure_message(error: Exception, *, fenced: bool) -> str:
    # Platform exceptions can contain commands/credentials. Only stable,
    # engine-owned messages cross the persisted/public boundary.
    if fenced:
        return "重启提交结果待确认，请查询实例状态；不会自动重复销毁或创建容器"
    reason = getattr(error, "reason", "")
    if isinstance(error, TimeoutError) or reason == "timeout":
        return "重启备份超时，本次重启已终止，旧容器未销毁"
    if reason in {
        "instance_changed",
        "binding_changed",
        "target_changed",
        "receipt_stale",
    }:
        return "重启目标或备份凭据已变化，本次重启已终止，旧容器未销毁"
    detail = {
        "backup_failed": "容器最终备份失败",
        "exec_failed": "执行容器备份命令失败",
        "invalid_response": "容器备份返回格式无效",
        "operation_mismatch": "容器备份操作标识不匹配",
        "unknown_status": "容器备份返回未知状态",
        "invalid_receipt": "缺少有效备份凭据",
        "generation_changed": "最终备份版本已变化",
        "helper_disappeared": "备份期间容器备份能力消失",
        "mount_changed": "备份期间挂载状态已变化",
        "missing_device": "无法定位旧容器",
    }.get(reason, "重启备份或前置检查失败")
    return f"{detail}，本次重启已终止，旧容器未销毁"


class AicodingDurableRestartMixin:
    async def execute_restart(self, ctx, restart, *, services=None, **kwargs):
        if str(ctx.active_engine or "").strip().lower() not in ENGINES:
            return restart(**kwargs)
        if services is None or services.task_queue is None:
            raise RuntimeError("重启任务队列不可用，未执行重启")
        # Only admission and persistence run here, never the backup or mutation.
        return await asyncio.to_thread(self._submit_restart, ctx, services, kwargs, restart)

    def _submit_restart(self, ctx, services, kwargs, restart=None):
        # Local import avoids a strategy <-> lifecycle implementation cycle and
        # preserves the existing domain error mapping at the HTTP boundary.
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotInvalidLifecycleStateError,
            BotServiceError,
        )

        bot_id, owner_id = kwargs["bot_id"], kwargs["user_id"]
        bot = services.get_bot(bot_id, owner_id)
        if not supports(bot) or bot.get("active_engine") != ctx.active_engine:
            raise BotServiceError("Bot 引擎已变化，请重新提交重启")
        if bot.get("bot_type") == "desktop":
            raise BotServiceError("Desktop Bot must use DesktopBotService")
        if not bot.get("entity_id"):
            raise BotServiceError("Bot has no entity_id; cannot restart")
        if bot.get("status") not in {"ACTIVE", "FAILED", "PENDING"}:
            raise BotInvalidLifecycleStateError(
                bot_id=bot_id, current_status=bot.get("status") or "UNKNOWN"
            )

        binding = bot.get("device_binding") or {}
        if bot.get("binding_id") is not None and not binding.get("device_id"):
            raise BotServiceError("无法确认当前设备绑定，未提交重启")
        key = task_key(bot_id, owner_id)
        existing = services.task_queue.find_by_idempotency_key(TASK_TYPE, key)
        if existing is not None and existing.status not in TERMINAL_STATUSES:
            payload = existing.payload
            result = _state(services.repository, payload).ensure(payload, existing.id)
            return self._accepted(result)

        # No old binding means no backup to wait for. Keep historical unbound
        # provider recovery intact instead of changing its FAILED input to
        # PENDING. Likewise retain the existing activation-in-progress guard.
        if bot.get("binding_id") is None or (
            binding.get("status") == "PENDING" and bot.get("status") != "PENDING"
        ):
            if restart is None:
                raise BotServiceError("Original restart callback is required")
            return restart(**kwargs)

        # Save template snapshots using their existing encryption/authorization
        # contract, not as plaintext credentials in a task-queue payload.
        try:
            self.apply_restart_extra_configs(
                ctx,
                kwargs.get("extra_configs"),
                template_service=services.template_service,
            )
        except Exception:
            logger.warning(
                "coding restart template update failed; retaining stored config: bot_id=%s",
                bot_id,
            )
        payload = {
            "bot_id": bot_id,
            "owner_id": owner_id,
            "tenant": get_current_avernet_tenant(),
            "operation_id": uuid.uuid4().hex,
            "engine": bot["active_engine"],
            "binding_id": bot.get("binding_id"),
            "previous_status": bot["status"],
            "device_id": binding.get("device_id"),
            "provider": binding.get("device_provider"),
            "nick_name": kwargs.get("nick_name"),
            "started_at": time.time(),
        }
        task, _created = services.task_queue.enqueue(
            TASK_TYPE,
            payload,
            deadline_seconds=TASK_DEADLINE,
            idempotency_key=key,
        )
        # A concurrent submit may have won queue dedup. Its payload, never ours,
        # owns the operation/target, even if its HTTP process died before CAS.
        result = _state(services.repository, task.payload).ensure(task.payload, task.id)
        return self._accepted(result)

    @staticmethod
    def _accepted(bot):
        result = dict(bot)
        result["restart_in_progress"] = journal(bot).get("phase") in IN_PROGRESS
        result["restart_operation_id"] = journal(bot).get("operation_id")
        return result


class AicodingRestartHandler:
    task_type = TASK_TYPE

    def __init__(
        self,
        *,
        repository,
        task_queue,
        bot_service_provider: Callable[[], BotServiceProtocol],
        publish_progress: Callable[[int], dict],
        clock=time.time,
    ):
        self.repository = repository
        self.task_queue = task_queue
        self.bot_service_provider = bot_service_provider
        self.publish_progress = publish_progress
        self.clock = clock

    def handle(self, payload):
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("tenant"), str)
            or not payload["tenant"]
        ):
            return Fail("missing restart tenant")
        with avernet_tenant_scope(payload["tenant"]):
            return self._handle(payload)

    def _handle(self, payload):
        if not isinstance(payload, dict) or not all(
            payload.get(k) for k in ("bot_id", "owner_id", "operation_id", "engine")
        ):
            return Fail("invalid coding restart payload")
        if str(payload["engine"]).strip().lower() not in ENGINES:
            return Fail("not a coding restart")
        state = _state(self.repository, payload)
        try:
            task = self.task_queue.find_by_idempotency_key(
                TASK_TYPE, task_key(state.bot_id, state.owner_id)
            )
            if task is None or task.payload.get("operation_id") != state.operation_id:
                return Complete()
            bot = state.ensure(payload, task.id)
            record = journal(bot)
            if record.get("phase") in TERMINAL:
                return (
                    Complete()
                    if record["phase"] == "SUCCEEDED"
                    else Fail(record.get("error_message") or "重启失败")
                )
            if not supports(bot) or bot.get("active_engine") != payload["engine"]:
                state.fail("Bot 引擎已变化，本次重启已终止")
                return Fail("engine changed")
            if self.clock() - payload["started_at"] >= BUSINESS_TIMEOUT:
                state.fail("重启等待超时，请检查实例状态；不会自动重复执行重启")
                return Fail("restart deadline exceeded")
            if record["phase"] == "WAITING_READY":
                return self._observe(state, record)
            if record["phase"] == "RESTARTING":
                # A lease-lost worker may still be inside the mutation. Keep
                # dedup ownership until its handoff appears or our deadline;
                # never release the key merely because a delivery saw the fence.
                return Reschedule(POLL_SECONDS)
            state.update({"QUEUED", "BACKING_UP"}, phase="BACKING_UP")
            return self._execute(state, payload)
        except RestartSuperseded:
            return self._superseded(state)

    def _execute(self, state, payload):
        execution = RestartExecution(state, payload)
        context_reset_handle = current_restart.set(execution)
        try:
            service = self.bot_service_provider()
            # Reload actual binding before any backup. The precondition and its
            # under-lock verifier fence a second time immediately before mutation.
            bot = service.get_bot(state.bot_id, state.owner_id)
            binding = bot.get("device_binding") or {}
            if (
                bot.get("binding_id") != payload["binding_id"]
                or bot.get("active_engine") != payload["engine"]
                or binding.get("device_id") != payload["device_id"]
                or binding.get("device_provider") != payload["provider"]
            ):
                state.fail("重启目标已变化，本次重启已终止，旧容器未销毁")
                return Fail("target changed")
            props = binding.get("device_props") or {}
            state.update(
                {"BACKING_UP"},
                source_request_id=props.get("restart_request_id"),
                source_publish_id=props.get("restart_publish_id"),
            )
            result = service.restart_bot(
                bot_id=state.bot_id,
                user_id=state.owner_id,
                nick_name=payload.get("nick_name"),
            )
            if not execution.fenced:
                # Existing lock/activation guards may return without handing off.
                # They are not proof that this operation restarted a container.
                return Reschedule(POLL_SECONDS)
            self._capture_completion(state, payload, result=result)
            return Reschedule(POLL_SECONDS)
        except RestartSuperseded:
            return self._superseded(state)
        except Exception as error:
            if execution.fenced:
                # Only observe durable provider intent; never reissue mutation
                # after an ambiguous response. No hook in the shared lifecycle.
                self._capture_completion(state, payload)
                return Reschedule(POLL_SECONDS)
            if (
                not execution.fenced
                and journal(state.read()).get("phase") == "RESTARTING"
            ):
                return Reschedule(POLL_SECONDS)
            message = _failure_message(error, fenced=execution.fenced)
            state.fail(message)
            logger.error(
                "coding restart blocked: bot_id=%s operation_id=%s error_type=%s",
                state.bot_id,
                state.operation_id,
                type(error).__name__,
            )
            return Fail(message)
        finally:
            current_restart.reset(context_reset_handle)

    def _capture_completion(self, state, payload, *, result=None):
        """Read existing provider records after the unmodified restart call.

        If the process dies before this observation is persisted, reclaimed
        RESTARTING deliveries wait for timeout rather than replaying mutation.
        """
        record = journal(state.read())
        if record.get("phase") != "RESTARTING":
            return
        bot = self.bot_service_provider().get_bot(state.bot_id, state.owner_id)
        binding = bot.get("device_binding") or {}
        props = binding.get("device_props") or {}
        if payload["provider"] == "baas":
            if bot.get("binding_id") != payload["binding_id"]:
                return
            request_id = props.get("restart_request_id")
            publish_id = props.get("restart_publish_id")
            new_request = request_id and request_id != record.get("source_request_id")
            new_publish = (
                publish_id is not None
                and str(publish_id) != str(record.get("source_publish_id"))
            )
            if not new_request and not (result is not None and new_publish):
                return
            handoff = {
                "provider": "baas", "binding_id": payload["binding_id"],
                "source_binding_id": payload["binding_id"],
                "restart_request_id": request_id, "publish_id": publish_id,
                "workflow_baseline": props.get("restart_workflow_baseline"),
            }
        else:
            # A failed stop/start call cannot prove allocation was submitted.
            if result is None:
                return
            target = bot.get("binding_id")
            handoff = {
                "provider": "allocation",
                "source_binding_id": payload["binding_id"],
                "binding_id": target if target != payload["binding_id"] else None,
            }
        state.update({"RESTARTING"}, phase="WAITING_READY", handoff=handoff)

    @staticmethod
    def _superseded(state):
        try:
            record = journal(state.read())
        except RestartSuperseded:
            return Complete()
        if (
            record.get("operation_id") == state.operation_id
            and record.get("phase") in IN_PROGRESS
        ):
            return Reschedule(POLL_SECONDS)
        return Complete()

    def _observe(self, state, record):
        bot = self.bot_service_provider().get_bot(state.bot_id, state.owner_id)
        handoff = record["handoff"]
        binding = bot.get("device_binding") or {}
        props = binding.get("device_props") or {}
        if handoff["provider"] == "baas":
            if bot.get("binding_id") != handoff["binding_id"]:
                state.fail("重启期间绑定已变化，请检查实例状态")
                return Fail("binding changed")
            publish_id = handoff.get("publish_id")
            if not publish_id:
                # Existing BaaS durable poller can adopt a publish after a lost
                # response. Adopt only the intent captured by this invocation.
                if (
                    handoff.get("restart_request_id")
                    and props.get("restart_request_id") == handoff["restart_request_id"]
                    and props.get("restart_publish_id")
                ):
                    handoff = {**handoff, "publish_id": props["restart_publish_id"]}
                    state.update({"WAITING_READY"}, handoff=handoff)
                    publish_id = handoff["publish_id"]
                else:
                    adopted = props.get("restart_publish_id")
                    baseline = handoff.get("workflow_baseline")
                    if (
                        props.get("restart_request_id") is None
                        and str(adopted or "").isdigit()
                        and isinstance(baseline, int)
                        and int(adopted) > baseline
                    ):
                        # The existing provider poller may finish and clear its
                        # intent before this observer sees it. The captured
                        # pre-submit baseline excludes the old publish.
                        state.update(
                            {"WAITING_READY"},
                            handoff={**handoff, "publish_id": adopted},
                        )
                        return Reschedule(POLL_SECONDS)
                    if props.get("restart_request_id") not in (
                        None,
                        handoff.get("restart_request_id"),
                    ):
                        state.fail("重启请求已被其他操作替换，请检查实例状态")
                        return Fail("restart intent changed")
                    if (
                        bot.get("status") == "FAILED"
                        and binding.get("status") == "FAILED"
                    ):
                        state.fail("本次重启部署失败，请检查部署日志")
                        return Fail("restart deployment failed")
                    return Reschedule(POLL_SECONDS)
            if not props.get("restart_publish_id"):
                return Reschedule(POLL_SECONDS)
            if str(props.get("restart_publish_id") or "") != str(publish_id):
                state.fail("重启部署记录已变化，请检查实例状态")
                return Fail("publish changed")
            progress = self.publish_progress(int(publish_id))
            status = str((progress or {}).get("status") or "").upper()
            if status in {"FAILED", "REJECTED", "REVOKED"}:
                state.fail("本次容器重启失败，请检查部署日志")
                return Fail("publish failed")
            if status != "SUCCESS":
                return Reschedule(POLL_SECONDS)
            # The workflow can succeed before the existing provider handler
            # finishes token refresh and persists the corresponding Bot result.
            # Require its exact completion marker, not an old runtime ACTIVE.
            if (
                str((bot.get("ext") or {}).get("restart_publish_id") or "")
                != str(publish_id)
                or props.get("restart_request_id") is not None
            ):
                return Reschedule(POLL_SECONDS)
        else:
            expected = handoff.get("binding_id")
            if expected is None and bot.get("binding_id") is not None:
                # Allocation persists its new binding asynchronously.
                if bot["binding_id"] == handoff["source_binding_id"]:
                    return Reschedule(POLL_SECONDS)
                handoff = {**handoff, "binding_id": bot["binding_id"]}
                state.update({"WAITING_READY"}, handoff=handoff)
                expected = bot["binding_id"]
            if expected is None:
                if bot.get("status") == "FAILED":
                    state.fail("本次容器分配失败，请检查启动日志")
                    return Fail("allocation failed")
                return Reschedule(POLL_SECONDS)
            if bot.get("binding_id") != expected:
                state.fail("重启期间绑定已变化，请检查实例状态")
                return Fail("binding changed")
        if bot.get("status") == "FAILED":
            state.fail("本次容器启动失败，请检查启动日志")
            return Fail("startup failed")
        if bot.get("binding_id") is not None and is_bot_ready(bot):
            state.update({"WAITING_READY"}, phase="SUCCEEDED", error_message=None)
            return Complete()
        return Reschedule(POLL_SECONDS)


class AicodingRestartLifecycle(LifecycleBase):
    def __init__(self, registry, handler):
        self.registry, self.handler = registry, handler

    async def bootstrap(self):
        self.registry.register(self.handler, wake_on_enqueue=True)
