"""acli operations: what a bot's CLI add, remove or cancel is while it runs.

Every add and remove of a bot's acli CLI is a task-queue task, and so is every
cancel (spec ``docs/specs/2026-10-21-acli-cli-operations/spec.md``, D6):

* add and remove of one runtime share the key ``acli:op:{bot_pk}:{stage}``, so
  a runtime runs at most one at a time;
* cancels have their own key, ``acli:cancel:{bot_pk}:{stage}``, so a cancel can
  be enqueued while the operation it targets is live.

The task's payload is fixed at enqueue. What the payload cannot hold — which
engine operation the task started — is the ``ac_acli_operation`` row, keyed by
the ``operation_id`` the payload carries (task handlers are never told their
task id). The task's own status and ``last_error`` stay the operation's status.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

#: Task types. One for add/remove, one for cancel: separate types keep the two
#: idempotency keys from ever sharing a dedup slot.
OPERATION_TASK_TYPE = "acli_clis.operation"
CANCEL_TASK_TYPE = "acli_clis.cancel"

#: Above the engine's own 30-minute operation limit, so the engine always
#: reports first and the task ends with its answer.
OPERATION_TASK_DEADLINE_SECONDS = 40 * 60
CANCEL_TASK_DEADLINE_SECONDS = 40 * 60
#: How often a running task asks the engine how its operation is doing.
POLL_SECONDS = 2.0

INSTALL = "install"
UNINSTALL = "uninstall"
ACTIONS = frozenset({INSTALL, UNINSTALL})

KIND_OPERATION = "operation"
KIND_CANCEL = "cancel"

RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

#: Engine operation states that are still in progress.
ENGINE_ACTIVE_STATES = frozenset({"running", "cancelling"})


def operation_key(bot_pk: int, stage: str) -> str:
    return f"acli:op:{bot_pk}:{stage}"


def cancel_key(bot_pk: int, stage: str) -> str:
    return f"acli:cancel:{bot_pk}:{stage}"


@dataclass(frozen=True, slots=True)
class AcliRuntime:
    """Which container an operation runs in: one stage of one bot."""

    bot_id: str
    #: The bot's owner; with ``bot_id`` it locates the bot row.
    owner_id: str
    stage: str


@dataclass(frozen=True, slots=True)
class EngineOperation:
    """The engine's record of one install or uninstall (its ``data.operation``)."""

    id: str
    action: str
    name: str
    state: str
    result: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None

    @property
    def active(self) -> bool:
        return self.state in ENGINE_ACTIVE_STATES

    @property
    def error_code(self) -> str:
        return str((self.error or {}).get("code") or self.state)

    @property
    def error_message(self) -> str:
        return str((self.error or {}).get("message") or f"the operation was {self.state}")


@dataclass(frozen=True, slots=True)
class AcliOperation:
    """One add, remove or cancel, as the API reports it."""

    id: str
    #: ``operation`` (an add or remove) or ``cancel``.
    kind: str
    #: ``install`` or ``uninstall`` — for a cancel, the action it cancels.
    action: str
    cli_code: str
    stage: str
    #: ``running``, ``succeeded``, ``failed`` or ``cancelled``.
    status: str
    #: ``granting``, ``installing``, ``uninstalling`` or ``cancelling`` while
    #: running; ``None`` once it has ended.
    step: str | None
    error_code: str | None = None
    error_message: str | None = None

    @property
    def running(self) -> bool:
        return self.status == RUNNING


@dataclass(frozen=True, slots=True)
class OperationPayload:
    """The task payload, fixed at enqueue. Identifiers and choices, never state."""

    operation_id: str
    kind: str
    action: str
    cli_code: str
    bot_pk: int
    bot_id: str
    #: Locates the bot row with ``bot_id`` (as the request did).
    entity_id: str
    #: The bot's owner: whose runtime it is.
    owner_id: str
    stage: str
    actor_id: str
    #: Wall-clock time of the enqueue, so a task can tell a row that is about
    #: to be written from one that never will be.
    enqueued_at: float
    #: The identity a granted tool gets (``owner`` / ``caller``); add only.
    identity_mode: str | None = None
    #: The market's description, stored with the grant; add only.
    cli_desc: str | None = None
    #: The operation a cancel targets; cancel only.
    target_operation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id, "kind": self.kind, "action": self.action,
            "cli_code": self.cli_code, "bot_pk": self.bot_pk, "bot_id": self.bot_id,
            "entity_id": self.entity_id, "owner_id": self.owner_id, "stage": self.stage,
            "actor_id": self.actor_id, "enqueued_at": self.enqueued_at,
            "identity_mode": self.identity_mode, "cli_desc": self.cli_desc,
            "target_operation_id": self.target_operation_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OperationPayload:
        return cls(
            operation_id=str(data["operation_id"]), kind=str(data["kind"]),
            action=str(data["action"]), cli_code=str(data["cli_code"]),
            bot_pk=int(data["bot_pk"]), bot_id=str(data["bot_id"]),
            entity_id=str(data["entity_id"]), owner_id=str(data["owner_id"]),
            stage=str(data["stage"]), actor_id=str(data["actor_id"]),
            enqueued_at=float(data.get("enqueued_at") or 0),
            identity_mode=data.get("identity_mode"), cli_desc=data.get("cli_desc"),
            target_operation_id=data.get("target_operation_id"),
        )

    @property
    def runtime(self) -> AcliRuntime:
        return AcliRuntime(bot_id=self.bot_id, owner_id=self.owner_id, stage=self.stage)


def task_error(code: str, message: str) -> str:
    """The ``Fail`` text a task ends with: ``"<code>: <message>"``."""
    return f"{code}: {message}"


def parse_task_error(text: str | None) -> tuple[str, str]:
    """``(code, message)`` from a task's ``last_error``.

    A failure this module did not write (the worker's own, a deadline) has no
    code of ours, so it reads as ``failed`` with the text as the message.
    """
    if not text:
        return FAILED, "the operation failed"
    code, sep, message = text.partition(": ")
    if sep and code and " " not in code:
        return code, message
    return FAILED, text


__all__ = [
    "ACTIONS",
    "CANCELLED",
    "CANCEL_TASK_DEADLINE_SECONDS",
    "CANCEL_TASK_TYPE",
    "ENGINE_ACTIVE_STATES",
    "FAILED",
    "INSTALL",
    "KIND_CANCEL",
    "KIND_OPERATION",
    "OPERATION_TASK_DEADLINE_SECONDS",
    "OPERATION_TASK_TYPE",
    "POLL_SECONDS",
    "RUNNING",
    "SUCCEEDED",
    "UNINSTALL",
    "AcliOperation",
    "AcliRuntime",
    "EngineOperation",
    "OperationPayload",
    "cancel_key",
    "operation_key",
    "parse_task_error",
    "task_error",
]
