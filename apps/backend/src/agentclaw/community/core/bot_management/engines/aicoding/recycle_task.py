"""Durable OpenAPI recycle: quick admission, read-only result, fenced execution."""

from __future__ import annotations

import hashlib
import json
import time
import uuid

from agentclaw.community.core.bot_management.engines.aicoding.backup_operation import (
    BackupPersistenceError,
)
from agentclaw.community.core.bot_management.engines.aicoding.recycle_state import (
    BLOCKING,
    POLL_SECONDS,
    RELEASE_TIMEOUT,
    RUNNING,
    TASK_DEADLINE,
    TASK_TYPE,
    TERMINAL,
    RecycleRuntime,
    RecycleSuperseded,
    RecycleTargetChanged,
    RecycleUnavailable,
    journal,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_arca import (
    RestartBackupTargetChanged,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_backup import (
    RestartBackupBlocked,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotInvalidLifecycleStateError,
    BotNotFoundError,
    BotOperationNotAllowedError,
)
from agentclaw.community.core.repository.protocols.bot.recycle import (
    BotRecycleRepositoryProtocol,
)
from agentclaw.community.core.task_queue.types import (
    TERMINAL_STATUSES,
    Complete,
    Fail,
    Reschedule,
    Retry,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)
from agentclaw.community.utils.env_utils import get_current_env

from .restart_state import supports


def task_key(bot_id, owner_id):
    return hashlib.sha256(
        json.dumps([get_current_avernet_tenant(), owner_id, bot_id]).encode()
    ).hexdigest()


def ensure_supported(bot, is_teclaw):
    if (
        bot.get("bot_type") != "personal"
        or not supports(bot)
        or is_teclaw(bot.get("active_engine"))
    ):
        raise BotOperationNotAllowedError(
            "Only personal managed-cloud Bots can be recycled"
        )


def public_result(bot, record):
    return {
        "bot_id": bot["bot_id"],
        "owner_id": bot["owner_id"],
        "operation_id": record["operation_id"],
        "bot_status": bot["status"],
        "phase": record["phase"],
        "error_code": record.get("error_code"),
        "message": record.get("message"),
        "warning": record.get("warning"),
    }


class AicodingRecycleOperations:
    def __init__(
        self,
        *,
        store: BotRecycleRepositoryProtocol,
        runtime: RecycleRuntime,
        queue,
        guard,
        app: str,
        worker_enabled: bool,
        is_teclaw,
        clock=time.time,
    ):
        self.store, self.runtime, self.queue, self.guard = store, runtime, queue, guard
        self.app, self.worker_enabled, self.clock = app, worker_enabled, clock
        self.is_teclaw = is_teclaw

    def submit(self, *, bot_id, owner_id, owner_name=None, request_id=""):
        bot = self.store.read(bot_id, owner_id)
        ensure_supported(bot, self.is_teclaw)
        with self.guard(bot):
            bot = self.store.read(bot_id, owner_id)
            record = journal(bot)
            existing = self.queue.find_by_idempotency_key(
                TASK_TYPE, task_key(bot_id, owner_id)
            )
            if (
                record.get("phase") in RUNNING
                and existing is not None
                and existing.payload.get("operation_id") == record.get("operation_id")
                and existing.status in TERMINAL_STATUSES
            ):
                # Repair a failed on_give_up write only on a mutating request;
                # GET remains strictly read-only. Never replay an uncertain release.
                record = self.store.update(
                    record,
                    RUNNING,
                    phase="NEEDS_CONFIRMATION"
                    if record["phase"] == "RELEASING"
                    else "FAILED",
                    error_code="TASK_INTERRUPTED",
                    message="回收任务已结束，请核对本次操作结果",
                )
            if record.get("phase") in BLOCKING:
                if record.get("app") != self.app:
                    raise BotInvalidLifecycleStateError(
                        bot_id=bot_id, current_status="RECYCLING"
                    )
                return self._accepted(bot, record, False)
            if bot["status"] == "RECYCLED":
                return {
                    "bot_id": bot_id,
                    "owner_id": owner_id,
                    "status": "RECYCLED",
                    "changed": False,
                    "operation_id": None,
                    "phase": None,
                }
            if existing is not None and existing.status not in TERMINAL_STATUSES:
                record = self.store.ensure(existing.payload, existing.id)
                return self._accepted(self.store.read(bot_id, owner_id), record, False)
            restart = (bot.get("ext") or {}).get("coding_restart") or {}
            if bot["status"] != "ACTIVE" or restart.get("phase") in {
                "QUEUED",
                "BACKING_UP",
                "RESTARTING",
                "WAITING_READY",
            }:
                raise BotInvalidLifecycleStateError(
                    bot_id=bot_id, current_status=bot["status"]
                )
            if not self.worker_enabled:
                raise RecycleUnavailable("Recycle worker is disabled")
            payload = {
                "bot_id": bot_id,
                "owner_id": owner_id,
                "bot_pk": bot["id"],
                "tenant": get_current_avernet_tenant(),
                "env": get_current_env(),
                "app": self.app,
                "engine": bot["active_engine"],
                "operation_id": uuid.uuid4().hex,
                "target": self.runtime.target(bot),
                "created_at": self.clock(),
                "source": "openapi",
                "request_id": request_id,
            }
            try:
                task, created = self.queue.enqueue(
                    TASK_TYPE,
                    payload,
                    deadline_seconds=TASK_DEADLINE,
                    idempotency_key=task_key(bot_id, owner_id),
                )
                record = self.store.ensure(task.payload, task.id)
            except RecycleSuperseded:
                raise BotInvalidLifecycleStateError(
                    bot_id=bot_id, current_status="CHANGED"
                ) from None
            except Exception as exc:
                raise RecycleUnavailable(
                    "Recycle acceptance could not be confirmed"
                ) from exc
            return self._accepted(self.store.read(bot_id, owner_id), record, created)

    @staticmethod
    def _accepted(bot, record, changed):
        return {
            "bot_id": bot["bot_id"],
            "owner_id": bot["owner_id"],
            "status": bot["status"],
            "changed": changed,
            "operation_id": record["operation_id"],
            "phase": record["phase"],
        }

    def get_operation(self, *, bot_id, owner_id, operation_id):
        bot = self.store.read(bot_id, owner_id)
        record = journal(bot)
        if (
            record.get("operation_id") != operation_id
            or record.get("app") != self.app
            or record.get("tenant") != get_current_avernet_tenant()
            or record.get("env") != get_current_env()
        ):
            raise BotNotFoundError("Recycle operation not found")
        return public_result(bot, record)


class AicodingRecycleHandler:
    task_type = TASK_TYPE

    def __init__(self, service: AicodingRecycleOperations, passport, clock=time.time):
        self.service, self.passport, self.clock = service, passport, clock

    def handle(self, payload):
        if (
            not isinstance(payload, dict)
            or not all(
                payload.get(k)
                for k in (
                    "tenant",
                    "env",
                    "app",
                    "bot_id",
                    "owner_id",
                    "bot_pk",
                    "operation_id",
                    "engine",
                )
            )
            or payload["env"] != get_current_env()
            or payload["app"] != self.service.app
        ):
            return Fail("Invalid recycle scope")
        with avernet_tenant_scope(payload["tenant"]):
            try:
                return self._handle(payload)
            except RecycleTargetChanged:
                # Target changes can reject THIS operation without making the
                # replacement Bot failed. An actually superseded delivery is a no-op.
                bot = self.service.store.read(payload["bot_id"], payload["owner_id"])
                record = journal(bot)
                if (
                    record.get("operation_id") == payload["operation_id"]
                    and record.get("phase") in RUNNING
                ):
                    self._fail(
                        payload,
                        record,
                        "TARGET_CHANGED",
                        "回收目标已变化，已停止本次操作",
                    )
                return Complete()
            except RecycleSuperseded:
                return Reschedule(POLL_SECONDS)
            except BotNotFoundError:
                return Complete()
            except Exception:
                # SQL/transport failures are not evidence of a backup veto or
                # resource release. Retry from the persisted phase, never replay
                # an already-fenced mutation. Do not store raw SDK exceptions.
                return Retry(
                    error="Recycle execution interrupted; retry persisted phase"
                )

    def _handle(self, payload):
        service, store = self.service, self.service.store
        task = service.queue.find_by_idempotency_key(
            TASK_TYPE, task_key(payload["bot_id"], payload["owner_id"])
        )
        if task is None or task.payload.get("operation_id") != payload["operation_id"]:
            return Complete()
        record = store.ensure(payload, task.id)
        if record["phase"] in TERMINAL:
            return (
                Complete()
                if record["phase"] == "SUCCEEDED"
                else Fail(record.get("error_code") or record["phase"])
            )
        if record["phase"] in {"QUEUED", "BACKING_UP"}:
            record = store.update(
                payload,
                {"QUEUED", "BACKING_UP"},
                phase="BACKING_UP",
                backup_started_at=record.get("backup_started_at", self.clock()),
            )
            bot = store.read(payload["bot_id"], payload["owner_id"])
            if (
                bot.get("bot_type") != "personal"
                or not supports(bot)
                or service.is_teclaw(bot.get("active_engine"))
                or bot["status"] != "ACTIVE"
                or bot["active_engine"] != record["engine"]
                or service.runtime.target(bot) != record["target"]
            ):
                raise RecycleTargetChanged("Recycle target changed")
            try:
                verify = service.runtime.prepare(
                    bot,
                    record,
                    lambda **changes: store.update(payload, {"BACKING_UP"}, **changes),
                )
                with service.guard(bot):
                    if verify is not None:
                        verify()
                    # This conditional transition is the one irreversible fence:
                    # only its winner may submit the provider destroy request.
                    record = store.update(
                        payload,
                        {"BACKING_UP"},
                        phase="RELEASING",
                        release_started_at=self.clock(),
                        release=None,
                    )
            except BackupPersistenceError:
                raise
            except RestartBackupBlocked as exc:
                reason = (
                    "BACKUP_TIMEOUT" if exc.reason == "timeout" else "BACKUP_FAILED"
                )
                store.update(
                    payload,
                    {"BACKING_UP"},
                    phase="FAILED",
                    error_code=reason,
                    message="备份失败或超时，本次回收已终止，未释放资源",
                )
                return Fail(reason)
            except RestartBackupTargetChanged:
                raise RecycleTargetChanged("Backup target changed") from None
            try:
                release = service.runtime.submit_release(
                    record["target"], payload["operation_id"], payload["owner_id"]
                )
            except Exception:
                # A lost response is ambiguous: observation, NOT another destroy.
                return Reschedule(POLL_SECONDS)
            record = store.update(payload, {"RELEASING"}, release=release)
        return self._observe(payload, record)

    def _observe(self, payload, record):
        service, store = self.service, self.service.store
        try:
            status = service.runtime.observe_release(
                record["target"], record.get("release")
            )
        except Exception:
            status = "UNKNOWN"
        if status != "RELEASED":
            if self.clock() - record["release_started_at"] >= RELEASE_TIMEOUT:
                store.update(
                    payload,
                    {"RELEASING"},
                    phase="NEEDS_CONFIRMATION",
                    error_code="RELEASE_UNCONFIRMED",
                    message="资源释放结果待确认，请勿重复回收或恢复",
                )
                return Fail("Release needs confirmation")
            return Reschedule(POLL_SECONDS)
        if not record.get("finalizer"):
            finalizer = uuid.uuid4().hex
            record = store.update(
                payload,
                {"RELEASING"},
                expected={"finalizer": None},
                finalizer=finalizer,
                release_confirmed=True,
            )
            try:
                self.passport.freeze_agent_passport(
                    bot_id=payload["bot_id"],
                    owner_workno=payload["owner_id"],
                    reason="dormant recycle",
                )
            except Exception:
                warning = record.get("warning") or {
                    "code": "PASSPORT_FREEZE_FAILED",
                    "message": "资源已释放，Passport 冻结失败",
                }
                record = store.update(
                    payload, {"RELEASING"}, warning=warning, passport_warning=True
                )
            record = store.update(payload, {"RELEASING"}, freeze_done=True)
        if record.get("freeze_done"):
            store.finish(payload, record["finalizer"])
            return Complete()
        # Another/previous delivery may still be in the Passport call. Never let
        # a later activation race a delayed freeze. Keep the operation protected.
        if self.clock() - record["release_started_at"] >= RELEASE_TIMEOUT:
            store.update(
                payload,
                {"RELEASING"},
                phase="NEEDS_CONFIRMATION",
                error_code="FINALIZATION_UNCONFIRMED",
                message="回收收尾结果待确认，暂不可恢复",
            )
            return Fail("Finalization needs confirmation")
        return Reschedule(POLL_SECONDS)

    def _fail(self, payload, record, code, message):
        phase = "NEEDS_CONFIRMATION" if record["phase"] == "RELEASING" else "FAILED"
        self.service.store.update(
            payload, RUNNING, phase=phase, error_code=code, message=message
        )

    def on_give_up(self, task):
        payload = task.payload
        if (
            payload.get("app") != self.service.app
            or payload.get("env") != get_current_env()
        ):
            return
        with avernet_tenant_scope(payload["tenant"]):
            bot = self.service.store.read(payload["bot_id"], payload["owner_id"])
            record = journal(bot)
            if (
                record.get("operation_id") == payload["operation_id"]
                and record.get("phase") in RUNNING
            ):
                self._fail(
                    payload, record, "TASK_TIMED_OUT", "回收任务未完成，请检查任务结果"
                )


class AicodingRecycleLifecycle(LifecycleBase):
    def __init__(self, registry, handler):
        self.registry, self.handler = registry, handler

    async def bootstrap(self):
        self.registry.register(self.handler, wake_on_enqueue=True)
