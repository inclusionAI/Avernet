"""Coding-only restart journal, CAS transitions and per-invocation backup fence.

The journal is stored in Bot.ext. Pre-mutation failures never change runtime
status/startup markers; post-mutation failures keep the existing lifecycle fields. A ContextVar
carries the durable operation through the *synchronous* legacy restart callback;
it never changes that callback's public signature or affects instance restarts.
"""

from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

KEY = "coding_restart"
TASK_TYPE = "aicoding.bot.restart"
ENGINES = frozenset({"aicoding", "claude_code"})
IN_PROGRESS = frozenset({"QUEUED", "BACKING_UP", "RESTARTING", "WAITING_READY"})
TERMINAL = frozenset({"SUCCEEDED", "FAILED"})
# Ordinary restart budget: original 25-minute backup plus the existing ten-minute
# provider observation window. Queue retention is not the business timeout.
BACKUP_TIMEOUT = 1500
BUSINESS_TIMEOUT = BACKUP_TIMEOUT + 600
TASK_DEADLINE = 86400
BACKUP_WARNING = 'backup_failed_continue'
BACKUP_WARNING_MESSAGE = '快盘备份失败，当前配置不阻断重启，存在数据丢失风险。'
published_backup_warning: ContextVar[Callable[[], None] | None] = ContextVar(
    'coding_published_backup_warning', default=None,
)


def supports(bot: dict) -> bool:
    return str(bot.get("active_engine") or "").strip().lower() in ENGINES


def task_key(bot_id: str, owner_id: str) -> str:
    # Bound key width and avoid delimiter collisions in user-controlled IDs.
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps([get_current_avernet_tenant(), owner_id, bot_id]).encode()
    ).hexdigest()


def journal(bot: dict) -> dict:
    value = (bot.get("ext") or {}).get(KEY)
    return value if isinstance(value, dict) else {}


def restart_status(ext: dict, *, published=False, progress=None, public=False):
    record = ext.get('restart' if published else KEY)
    if not isinstance(record, dict) or not isinstance(record.get('operation_id'), str):
        return None
    phase = record.get('phase')
    status = record.get('status') if published else (
        phase if phase in TERMINAL else 'RUNNING' if phase in IN_PROGRESS else None
    )
    if status not in {'RUNNING', 'FAILED', 'SUCCEEDED'}:
        return None
    if published and status == 'RUNNING' and phase == 'WAITING_READY' and progress:
        status = {'SUCCESS': 'SUCCEEDED', 'FAILED': 'FAILED'}.get(progress.get('status'), 'RUNNING')
    result = {
        'operation_id': record['operation_id'], 'status': status,
        'phase': record.get('failure_phase', phase),
        'message': record.get('error_message') or record.get('message') or (
            '容器重启失败，请检查部署日志' if status == 'FAILED' else None
        ),
    }
    if record.get('backup_warning') == BACKUP_WARNING:
        result['warning'] = {'code': BACKUP_WARNING, 'message': BACKUP_WARNING_MESSAGE}
    if public:
        result['message'] = None
        if status == 'FAILED':
            result['message'] = (
                '快盘备份失败，本次重启已终止，旧容器未销毁；请查看 Bot 日志。'
                if result['phase'] == 'BACKING_UP'
                else '本次重启失败，请查看 Bot 日志。'
            )
    return result


def project_status_ext(ext, *, response_ext, published=False, progress=None, public=False):
    """Merge the response namespace without mutating persisted JSON or exposing it."""
    result = deepcopy(response_ext)
    restart = restart_status(ext, published=published, progress=progress, public=public)
    if restart is not None:
        namespace = result.setdefault('aicoding', {})
        namespace['restart'] = restart
    return result


def published_restart_result(record):
    """Backup/precondition outcomes must not read an older BaaS workflow."""
    from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult

    restart = restart_status(record.ext or {}, published=True)
    if restart is None or restart['phase'] not in {'QUEUED', 'BACKING_UP'}:
        return None
    return PublishFlowResult(
        publish_id=record.id, status=record.status, action='sync_restart',
        success=restart['status'] != 'FAILED',
        message=restart['message'] or '重启前置检查进行中',
        ext=project_status_ext(record.ext or {}, response_ext={}, published=True),
    )


def record_nonblocking_backup_warning(operation_id):
    """Persist only a confirmed fastdisk backup failure that was allowed through."""
    execution = current_restart.get()
    if execution is not None:
        if execution.operation_id != operation_id:
            raise RestartSuperseded('Backup warning belongs to another restart')
        if journal(execution.state.read()).get('backup_warning') != BACKUP_WARNING:
            execution.state.update(IN_PROGRESS, backup_warning=BACKUP_WARNING)
    else:
        report = published_backup_warning.get()
        if report is not None:
            report()


def published_warning_reporter(services, *, publish_id, guard_id):
    def report():
        def warn(ext):
            state = ext.get('restart') or {}
            if (state.get('guard') or {}).get('id') == guard_id:
                state['backup_warning'] = BACKUP_WARNING
        services.mutate_ext(publish_id=publish_id, mutator=warn)
    return report


class RestartSuperseded(RuntimeError):
    """This delivery no longer owns the journal; it must not issue side effects."""


@dataclass
class RestartState:
    repository: Any
    bot_id: str
    owner_id: str
    operation_id: str

    def read(self) -> dict:
        bot = self.repository.get_by_id_and_owner(self.bot_id, self.owner_id)
        if not isinstance(bot, dict):
            raise RestartSuperseded("Bot no longer exists")
        return bot

    def update(self, phases: set | frozenset, **changes) -> dict:
        for _ in range(12):
            bot = self.read()
            old = journal(bot)
            if (
                old.get("operation_id") != self.operation_id
                or old.get("phase") not in phases
            ):
                raise RestartSuperseded("Restart operation or phase changed")
            ext = deepcopy(bot.get("ext") or {})
            ext[KEY] = {**old, **changes}
            if 'fastdisk_targets' in changes:
                # Monotone across concurrent deliveries and CAS retries.
                ext[KEY]['fastdisk_targets'] = sorted(set(old.get('fastdisk_targets', []))
                                                    | set(changes['fastdisk_targets']))
            if changes.get('phase') == 'RESTARTING':
                # Startup markers belong to the replacement only after mutation
                # is authorized; backup-only failures preserve the old markers.
                ext.pop('start_status', None)
                ext.pop('start_message', None)
            failed = changes.get("phase") == "FAILED"
            if failed:
                ext[KEY]['failure_phase'] = old.get('phase')
                ext[KEY]['runtime_preserved'] = old.get('phase') in {'QUEUED', 'BACKING_UP'}
            # Only post-submission failures use the runtime startup-error
            # contract. The journal independently exposes operation progress.
            # Never mark a concurrently replaced instance failed.
            target_binding = old.get("handoff", {}).get(
                "binding_id", old.get("binding_id")
            )
            if failed:
                ext[KEY]["failed_binding_id"] = target_binding
            owns_target = (
                supports(bot)
                and bot.get("active_engine") == old.get("engine", bot.get("active_engine"))
                and bot.get("binding_id") == target_binding
            )
            if failed and owns_target and not ext[KEY]['runtime_preserved']:
                ext["start_status"] = "FAILED"
                ext["start_message"] = changes["error_message"]
            updated = self.repository.compare_and_set_ext(
                bot_id=self.bot_id,
                owner_id=self.owner_id,
                expected_ext=bot.get("ext"),
                ext=ext,
            )
            if updated is not None:
                return self._sync_status(updated) if failed else updated
        raise RuntimeError("Restart journal update contention")

    def ensure(self, payload: dict, task_id: int) -> dict:
        """Both submitter and worker can repair an enqueue-before-journal crash.

        Queue dedup is the admission authority. The monotonic task row ID prevents
        a delayed submitter overwriting a subsequent operation or its outcome.
        Admission preserves the serving state; ensure repairs only the journal
        and post-mutation failure status.
        """
        for _ in range(12):
            bot = self.read()
            old = journal(bot)
            if old.get("operation_id") == self.operation_id:
                return self._sync_status(bot)
            if int(old.get("task_id") or 0) >= task_id:
                raise RestartSuperseded("A newer restart owns this Bot")
            matches = (
                supports(bot)
                and bot.get("active_engine") == payload["engine"]
                and bot.get("binding_id") == payload["binding_id"]
                and bot.get("status") == payload["previous_status"]
            )
            record = {
                "operation_id": self.operation_id,
                "task_id": task_id,
                "phase": "QUEUED" if matches else "FAILED",
                "error_message": None
                if matches
                else "重启目标或状态已变化，本次重启未执行",
                "binding_id": payload["binding_id"],
                "engine": payload["engine"],
                "started_at": payload["started_at"],
            }
            ext = deepcopy(bot.get("ext") or {})
            ext[KEY] = record
            result = self.repository.compare_and_set_ext(
                bot_id=self.bot_id,
                owner_id=self.owner_id,
                expected_ext=bot.get("ext"),
                ext=ext,
            )
            if result is not None:
                return self._sync_status(result) if matches else result
        raise RuntimeError("Restart journal initialization contention")

    def _sync_status(self, bot: dict) -> dict:
        """Reuse status-only updates without overwriting the CAS-owned journal.

        This is deliberately NOT a lifecycle CAS. Re-read operation/target to
        reject already superseded writes, but another writer can still race
        between this read and update_by_owner. The repository contract is kept
        unchanged; phase/mutation fencing retains the original ext-only CAS.
        """
        record = journal(bot)
        phase = record.get("phase")
        if phase != "FAILED" or record.get("runtime_preserved") is True:
            return bot
        target = record.get("failed_binding_id", record.get("binding_id"))
        current = self.read()
        current_record = journal(current)
        if (
            current_record.get("operation_id") != self.operation_id
            or current_record.get("phase") != phase
            or current.get("binding_id") != target
            or not supports(current)
            or current.get("active_engine") != record.get("engine")
        ):
            return current
        status = "FAILED"
        # A rejected admission has no matching public failure marker.
        if phase == "FAILED" and (
            (current.get("ext") or {}).get("start_status") != "FAILED"
            or (current.get("ext") or {}).get("start_message")
            != current_record.get("error_message")
        ):
            return current
        if current.get("status") == status:
            return current
        updated = self.repository.update_by_owner(
            self.bot_id, self.owner_id, {"status": status}
        )
        if updated is None:
            raise RestartSuperseded("Bot disappeared while saving restart status")
        return updated

    def fail(self, message: str) -> dict:
        return self.update(
            IN_PROGRESS,
            phase="FAILED",
            error_message=message,
        )


@dataclass
class RestartExecution:
    state: RestartState
    payload: dict
    # An in-memory flag distinguishes a known pre-side-effect error from a lost
    # response after the durable mutation fence. It is never used for recovery.
    fenced: bool = False
    verify_backup: Callable[[], None] | None = None

    @property
    def operation_id(self) -> str:
        return self.state.operation_id

    def check_target(self, ctx, binding_id) -> None:
        bot = self.state.read()
        record = journal(bot)
        if (
            record.get("operation_id") != self.operation_id
            or record.get("phase") != "BACKING_UP"
        ):
            raise RestartSuperseded("Restart no longer owns backup")
        if (
            ctx.bot_id != self.state.bot_id
            or ctx.owner_id != self.state.owner_id
            or ctx.active_engine != self.payload["engine"]
            or binding_id != self.payload["binding_id"]
            or bot.get("binding_id") != binding_id
        ):
            raise RuntimeError("备份期间 Bot 或绑定已变化，禁止替换")

    def fence_mutation(self) -> None:
        # Invoked only AFTER the existing under-lock receipt/identity verifier.
        # A second delivery can back up the same operation but cannot cross this
        # CAS fence twice, even if a worker loses its lease while executing.
        import time

        if time.time() - self.payload["started_at"] >= BUSINESS_TIMEOUT:
            raise TimeoutError("Restart deadline elapsed before mutation")
        bot = self.state.read()
        if (
            bot.get("binding_id") != self.payload["binding_id"]
            or bot.get("active_engine") != self.payload["engine"]
        ):
            raise RuntimeError("重启目标已变化，禁止替换")
        self.state.update({"BACKING_UP"}, phase="RESTARTING")
        self.fenced = True
        # The old runtime is retained through backup. Only the mutation owner
        # enters the existing deployment state, immediately before provider I/O.
        if self.state.repository.update_by_owner(
            self.state.bot_id, self.state.owner_id, {'status': 'PENDING'}
        ) is None:
            raise RestartSuperseded('Bot disappeared before restart submission')


current_restart: ContextVar[RestartExecution | None] = ContextVar(
    "coding_restart", default=None
)
