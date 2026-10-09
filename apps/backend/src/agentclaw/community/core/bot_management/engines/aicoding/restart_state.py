"""Coding-only restart journal, CAS transitions and per-invocation backup fence.

The journal is stored in Bot.ext; terminal failures also use the existing
Bot status and start_status/start_message fields consumed by status polling. A ContextVar
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
            failed = changes.get("phase") == "FAILED"
            # Reuse the existing public startup-error contract. The journal's
            # copy remains an operation-scoped audit/recovery record, not a new
            # status response field. Never mark a replacement instance failed.
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
            if failed and owns_target:
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

    def initialize(
        self, payload: dict, previous_operation_id: str | None,
        *, replace_terminal_task: bool = False,
    ) -> dict:
        """HTTP admission alone claims the journal and writes PENDING before enqueue.

        A concurrent admission joins the winner without repeating its status write.
        No worker repairs admission or the process-exit-before-enqueue window.
        """
        for _ in range(12):
            bot = self.read()
            old = journal(bot)
            if old.get("phase") in IN_PROGRESS and not (
                replace_terminal_task and old.get("operation_id") == previous_operation_id
            ):
                return bot
            if old.get("operation_id") != previous_operation_id:
                raise RestartSuperseded("A newer restart owns this Bot")
            if not (
                supports(bot)
                and bot.get("active_engine") == payload["engine"]
                and bot.get("binding_id") == payload["binding_id"]
                and bot.get("status") == payload["previous_status"]
            ):
                raise RestartSuperseded("Restart target or status changed")
            ext = deepcopy(bot.get("ext") or {})
            ext[KEY] = {
                "operation_id": self.operation_id,
                "phase": "QUEUED",
                "error_message": None,
                "binding_id": payload["binding_id"],
                "engine": payload["engine"],
                "started_at": payload["started_at"],
            }
            ext.pop("start_status", None)
            ext.pop("start_message", None)
            result = self.repository.compare_and_set_ext(
                bot_id=self.bot_id, owner_id=self.owner_id,
                expected_ext=bot.get("ext"), ext=ext,
            )
            if result is not None:
                return self._sync_status(result)
        raise RuntimeError("Restart journal initialization contention")

    def read_for_task(self) -> dict:
        """Observe the admitted operation; only failure finalization may write status."""
        bot = self.read()
        record = journal(bot)
        if record.get("operation_id") != self.operation_id:
            raise RestartSuperseded("Restart task no longer owns the journal")
        return self._sync_status(bot) if record.get("phase") == "FAILED" else bot

    def _sync_status(self, bot: dict) -> dict:
        """Reuse status-only updates without overwriting the CAS-owned journal.

        This is deliberately NOT a lifecycle CAS. Re-read operation/target to
        reject already superseded writes, but another writer can still race
        between this read and update_by_owner. The repository contract is kept
        unchanged; phase/mutation fencing retains the original ext-only CAS.
        """
        record = journal(bot)
        phase = record.get("phase")
        if phase not in {"QUEUED", "FAILED"}:
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
        status = "FAILED" if phase == "FAILED" else "PENDING"
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
            current = self.read()
            record_now = journal(current)
            if (
                record_now.get("operation_id") == self.operation_id
                and record_now.get("phase") == phase
                and current.get("binding_id") == target
                and current.get("active_engine") == record.get("engine")
                and current.get("status") == status
            ):
                return current
            raise RestartSuperseded("Bot status write was not confirmed")
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


current_restart: ContextVar[RestartExecution | None] = ContextVar(
    "coding_restart", default=None
)
