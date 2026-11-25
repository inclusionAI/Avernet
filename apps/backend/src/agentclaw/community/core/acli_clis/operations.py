"""acli operations: what a bot's CLI add, remove or cancel is while it runs.

Every add and remove of a bot's acli CLI is a task-queue task, and so is every
cancel (spec ``apps/backend/specs/2026-10-21-acli-cli-operations/spec.md``, D6):

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
from enum import StrEnum
from typing import Any, Mapping

from agentclaw.community.core.service_bot.types import PublishStage

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


class AcliAction(StrEnum):
    """What an engine operation does to a tool."""

    INSTALL = "install"
    UNINSTALL = "uninstall"


class OperationKind(StrEnum):
    """An add or remove (``operation``), or a ``cancel`` of one."""

    OPERATION = "operation"
    CANCEL = "cancel"


class OperationStatus(StrEnum):
    """Where an add, remove or cancel stands, as the API reports it."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OperationStep(StrEnum):
    """What a running operation is doing now."""

    GRANTING = "granting"
    INSTALLING = "installing"
    UNINSTALLING = "uninstalling"
    CANCELLING = "cancelling"


class EngineState(StrEnum):
    """The engine operation's ``state`` (engine contract)."""

    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def active(self) -> bool:
        return self in (EngineState.RUNNING, EngineState.CANCELLING)


def operation_key(bot_pk: int, stage: PublishStage) -> str:
    return f"acli:op:{bot_pk}:{stage.value}"


def cancel_key(bot_pk: int, stage: PublishStage) -> str:
    return f"acli:cancel:{bot_pk}:{stage.value}"


@dataclass(frozen=True, slots=True)
class AcliRuntime:
    """Which container an operation runs in: one stage of one bot."""

    bot_id: str
    #: The bot's owner; with ``bot_id`` it locates the bot row.
    owner_id: str
    stage: PublishStage
    #: The bot's ``active_engine``; it picks the engine's acli endpoint prefix.
    engine: str = ""


@dataclass(frozen=True, slots=True)
class EngineOperation:
    """The engine's record of one install or uninstall (its ``data.operation``)."""

    id: str
    action: AcliAction
    name: str
    state: EngineState
    result: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None

    @property
    def active(self) -> bool:
        return self.state.active

    @property
    def error_code(self) -> str:
        return str((self.error or {}).get("code") or self.state.value)

    @property
    def error_message(self) -> str:
        return str(
            (self.error or {}).get("message") or f"the operation was {self.state.value}"
        )


@dataclass(frozen=True, slots=True)
class AcliOperation:
    """One add, remove or cancel, as the API reports it."""

    id: str
    kind: OperationKind
    #: For a cancel, the action it cancels.
    action: AcliAction
    cli_code: str
    stage: PublishStage
    status: OperationStatus
    #: ``None`` once it has ended.
    step: OperationStep | None
    error_code: str | None = None
    error_message: str | None = None

    @property
    def running(self) -> bool:
        return self.status == OperationStatus.RUNNING


@dataclass(frozen=True, slots=True)
class OperationPayload:
    """The task payload, fixed at enqueue. Identifiers and choices, never state."""

    operation_id: str
    kind: OperationKind
    action: AcliAction
    cli_code: str
    bot_pk: int
    bot_id: str
    #: Locates the bot row with ``bot_id`` (as the request did).
    entity_id: str
    #: The bot's owner: whose runtime it is.
    owner_id: str
    stage: PublishStage
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
    #: The bot's ``active_engine`` (``AcliRuntime.engine``); empty in a payload
    #: enqueued before it was recorded, which only an adapter engine could take.
    engine: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id, "kind": self.kind.value,
            "action": self.action.value, "cli_code": self.cli_code, "bot_pk": self.bot_pk,
            "bot_id": self.bot_id, "entity_id": self.entity_id, "owner_id": self.owner_id,
            "stage": self.stage.value,
            "actor_id": self.actor_id, "enqueued_at": self.enqueued_at,
            "identity_mode": self.identity_mode, "cli_desc": self.cli_desc,
            "target_operation_id": self.target_operation_id, "engine": self.engine,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OperationPayload:
        return cls(
            operation_id=str(data["operation_id"]), kind=OperationKind(data["kind"]),
            action=AcliAction(data["action"]), cli_code=str(data["cli_code"]),
            bot_pk=int(data["bot_pk"]), bot_id=str(data["bot_id"]),
            entity_id=str(data["entity_id"]), owner_id=str(data["owner_id"]),
            stage=PublishStage(data["stage"]), actor_id=str(data["actor_id"]),
            enqueued_at=float(data.get("enqueued_at") or 0),
            identity_mode=data.get("identity_mode"), cli_desc=data.get("cli_desc"),
            target_operation_id=data.get("target_operation_id"),
            engine=str(data.get("engine") or ""),
        )

    @property
    def runtime(self) -> AcliRuntime:
        return AcliRuntime(
            bot_id=self.bot_id, owner_id=self.owner_id, stage=self.stage, engine=self.engine
        )


def task_error(code: str, message: str) -> str:
    """The ``Fail`` text a task ends with: ``"<code>: <message>"``."""
    return f"{code}: {message}"


def parse_task_error(text: str | None) -> tuple[str, str]:
    """``(code, message)`` from a task's ``last_error``.

    A failure this module did not write (the worker's own, a deadline) has no
    code of ours, so it reads as ``failed`` with the text as the message.
    """
    if not text:
        return OperationStatus.FAILED.value, "the operation failed"
    code, sep, message = text.partition(": ")
    if sep and code and " " not in code:
        return code, message
    return OperationStatus.FAILED.value, text


__all__ = [
    "CANCEL_TASK_DEADLINE_SECONDS",
    "CANCEL_TASK_TYPE",
    "OPERATION_TASK_DEADLINE_SECONDS",
    "OPERATION_TASK_TYPE",
    "POLL_SECONDS",
    "AcliAction",
    "AcliOperation",
    "AcliRuntime",
    "EngineOperation",
    "EngineState",
    "OperationKind",
    "OperationPayload",
    "OperationStatus",
    "OperationStep",
    "cancel_key",
    "operation_key",
    "parse_task_error",
    "task_error",
]
