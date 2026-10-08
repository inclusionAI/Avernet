"""Coding-only restart journal, CAS transitions and per-invocation backup fence.

The journal is stored in Bot.ext, not in device error/startup fields. A ContextVar
carries the durable operation through the *synchronous* legacy restart callback;
it never changes that callback's public signature or affects instance restarts.
"""

from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

KEY = "coding_restart"
TASK_TYPE = "aicoding.bot.restart"
ENGINES = frozenset({"aicoding", "claude_code"})
IN_PROGRESS = frozenset({"QUEUED", "BACKING_UP", "RESTARTING", "WAITING_READY"})
TERMINAL = frozenset({"SUCCEEDED", "FAILED"})
# Independent from the unchanged five-minute browser polling window.
BUSINESS_TIMEOUT = 7200
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
            updated = self.repository.compare_and_set_ext(
                bot_id=self.bot_id,
                owner_id=self.owner_id,
                expected_ext=bot.get("ext"),
                ext=ext,
            )
            if updated is not None:
                return updated
        raise RuntimeError("Restart journal update contention")

    def ensure(self, payload: dict, task_id: int) -> dict:
        """Both submitter and worker can repair an enqueue-before-journal crash.

        Queue dedup is the admission authority. The monotonic task row ID prevents
        a delayed submitter overwriting a subsequent operation or its outcome.
        PENDING and the journal become visible in the same CAS statement.
        """
        for _ in range(12):
            bot = self.read()
            old = journal(bot)
            if old.get("operation_id") == self.operation_id:
                return bot
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
                "started_at": payload["started_at"],
            }
            ext = deepcopy(bot.get("ext") or {})
            ext[KEY] = record
            result = self.repository.compare_and_set_ext(
                bot_id=self.bot_id,
                owner_id=self.owner_id,
                expected_ext=bot.get("ext"),
                ext=ext,
                expected_state={
                    field: bot.get(field)
                    for field in ("status", "binding_id", "active_engine")
                },
                status="PENDING" if matches else None,
            )
            if result is not None:
                return result
        raise RuntimeError("Restart journal initialization contention")

    def fail(self, message: str) -> dict:
        return self.update(
            IN_PROGRESS,
            phase="FAILED",
            error_message=message,
            failed_binding_id=self.read().get("binding_id"),
        )


@dataclass
class RestartExecution:
    state: RestartState
    payload: dict
    # An in-memory flag distinguishes a known pre-side-effect error from a lost
    # response after the durable mutation fence. It is never used for recovery.
    fenced: bool = False

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
