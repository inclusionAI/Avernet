"""Task-queue handlers that carry an acli add, remove or cancel to its end.

Each run is one short step — submit, or read the engine operation — and then
``Reschedule(2 s)`` until the engine operation ends; the engine operation's
end decides the task's. A run can be repeated at any point (the queue is
at-least-once), so every step first looks at what already happened:

* the engine operation a task started is the ``ac_acli_operation`` row's
  ``engine_operation_id``; with one, the task only reads it;
* a submit whose id was never recorded (a lost lease between the submit and
  the write) is found again when the next submit is refused ``engine_busy``
  with an operation of the same action and name. If that operation already
  ended, the second submit is a no-op install (``already_installed``) or
  uninstall (``uninstalled: false``).

Grants are written before an install and never revoked (spec D9).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Mapping

from agentclaw.community.core.acli_clis.operations import (
    CANCEL_TASK_TYPE,
    AcliAction,
    EngineState,
    OPERATION_TASK_TYPE,
    POLL_SECONDS,
    EngineOperation,
    OperationPayload,
    task_error,
)
from agentclaw.community.core.acli_clis.port import AcliPort, AcliPortError
from agentclaw.community.core.acli_clis.repository.store import AcliOperationStore
from agentclaw.community.core.caller_identity.guard import load_bot
from agentclaw.community.core.mcp.services.cli_passport_scope import (
    CliPassportScopeReconciler,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.platform import (
    TaskQueueRepositoryProtocol,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import (
    TERMINAL_STATUSES,
    Complete,
    Fail,
    Reschedule,
    TaskOutcome,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

logger = get_logger()

#: Port codes after which asking again may succeed: the hop failed, or the
#: container is busy with an operation that is not this task's.
_TRANSIENT_CODES = frozenset({"timeout", "engine_unreachable", "engine_busy"})
#: How long a task waits for the API to write its ``ac_acli_operation`` row
#: (the row is written right after the enqueue) before giving up on it.
RECORD_GRACE_SECONDS = 60.0


def _resolved_name(op: EngineOperation, payload: OperationPayload) -> str | None:
    """The market name acli installed, when it is not the code that was asked for.

    That happens when ``cli_code`` was a command alias the market could not be
    asked about before the submit.
    """
    if payload.action != AcliAction.INSTALL or op.state != EngineState.SUCCEEDED:
        return None
    installed = str((op.result or {}).get("name") or payload.cli_code)
    return installed if installed != payload.cli_code else None


def _unrecorded(payload: OperationPayload) -> TaskOutcome:
    if time.time() - payload.enqueued_at < RECORD_GRACE_SECONDS:
        return Reschedule(POLL_SECONDS)
    logger.error("acli_operation_unrecorded operation_id=%s", payload.operation_id)
    return Fail(task_error("not_recorded", "the operation was never recorded"))


class AcliOperationTaskHandler:
    """Runs one add (grant, then install) or remove (uninstall)."""

    def __init__(
        self,
        *,
        store: AcliOperationStore,
        port: AcliPort,
        bots: BotRepository,
        reconciler: CliPassportScopeReconciler,
    ) -> None:
        self._store = store
        self._port = port
        self._bots = bots
        self._reconciler = reconciler

    @property
    def task_type(self) -> str:
        return OPERATION_TASK_TYPE

    def handle(self, payload: dict | None) -> TaskOutcome:
        if not payload:
            logger.error("acli_operation_task empty payload")
            return Complete()
        return asyncio.run(self._step(payload))

    async def _step(self, raw: Mapping[str, Any]) -> TaskOutcome:
        payload = OperationPayload.from_dict(raw)
        record = await asyncio.to_thread(self._store.get, payload.operation_id)
        if record is None:
            return _unrecorded(payload)
        if record.engine_operation_id is not None:
            return await self._poll(payload, record.engine_operation_id)

        if payload.action == AcliAction.INSTALL:
            refused = await asyncio.to_thread(self._ensure_granted, payload)
            if refused is not None:
                return refused
        try:
            op = await self._port.submit(
                payload.runtime, action=payload.action, name=payload.cli_code
            )
        except AcliPortError as error:
            mine = error.operation
            if error.code == "engine_busy" and mine is not None and (
                mine.action == payload.action and mine.name == payload.cli_code
            ):
                # This task's own earlier submit, whose id was never recorded.
                op = mine
            elif error.code in _TRANSIENT_CODES:
                return Reschedule(POLL_SECONDS)
            else:
                logger.warning(
                    "acli_operation_submit_failed operation_id=%s code=%s",
                    payload.operation_id, error.code,
                )
                return Fail(task_error(error.code, error.message))
        await asyncio.to_thread(
            self._store.set_engine_operation_id, payload.operation_id, op.id
        )
        logger.info(
            "acli_operation_submitted operation_id=%s engine_operation_id=%s %s %s",
            payload.operation_id, op.id, payload.action, payload.cli_code,
        )
        return await self._outcome(op, payload)

    async def _poll(self, payload: OperationPayload, engine_operation_id: str) -> TaskOutcome:
        try:
            op = await self._port.get_operation(payload.runtime, engine_operation_id)
        except AcliPortError as error:
            if error.code in _TRANSIENT_CODES:
                return Reschedule(POLL_SECONDS)
            return Fail(task_error(error.code, error.message))
        if op is None:
            return Fail(task_error(
                "operation_lost", "the engine no longer has a record of the operation"
            ))
        return await self._outcome(op, payload)

    async def _outcome(self, op: EngineOperation, payload: OperationPayload) -> TaskOutcome:
        """The task's outcome once its engine operation is known."""
        if op.active:
            return Reschedule(POLL_SECONDS)
        if op.state != EngineState.SUCCEEDED:
            return Fail(task_error(op.error_code, op.error_message))
        resolved = _resolved_name(op, payload)
        if resolved is None:
            return Complete()
        # The tool is installed under its market name, so grant that name too:
        # installed ⇒ granted holds, and a retry with the market name finds it
        # installed and granted. The alias's own grant stays (D9).
        refused = await asyncio.to_thread(self._ensure_granted, payload, resolved)
        if refused is not None:
            return refused
        logger.warning(
            "acli_operation_name_mismatch operation_id=%s cli_code=%s installed=%s",
            payload.operation_id, payload.cli_code, resolved,
        )
        return Fail(task_error(
            "name_mismatch", f"{payload.cli_code!r} is a command; use {resolved!r}"
        ))

    def _ensure_granted(
        self, payload: OperationPayload, cli_code: str | None = None
    ) -> TaskOutcome | None:
        """Grant ``cli_code`` (default: the payload's); ``Fail`` if the grant does not hold.

        Every grant is read back: a Passport can accept a write it does not keep.
        A grant that is already there is left as it is.
        """
        cli_code = cli_code or payload.cli_code
        bot = load_bot(self._bots, bot_id=payload.bot_id, entity_id=payload.entity_id)
        if cli_code in self._granted(bot):
            return None
        write_error: Exception | None = None
        try:
            self._reconciler.add_cli(
                bot=bot,
                item={
                    "cli_code": cli_code,
                    "cli_name": cli_code,
                    "cli_desc": payload.cli_desc or "",
                    "identity_mode": payload.identity_mode or "owner",
                },
            )
        except Exception as error:  # noqa: BLE001 — judged by the read-back
            write_error = error
        if cli_code in self._granted(bot):
            return None
        logger.error(
            "acli_operation_grant_failed operation_id=%s cli_code=%s error_type=%s",
            payload.operation_id, cli_code,
            type(write_error).__name__ if write_error else None,
        )
        return Fail(task_error("grant_failed", "the CLI grant could not be written"))

    def _granted(self, bot: Mapping[str, Any]) -> set[str]:
        return {
            str(item["cli_code"]) for item in self._reconciler.current_passport_cli_items(bot=bot)
        }


class AcliCancelTaskHandler:
    """Forwards a cancel once its target reached the engine, then waits for the end."""

    def __init__(
        self,
        *,
        store: AcliOperationStore,
        port: AcliPort,
        tasks: TaskQueueRepositoryProtocol,
    ) -> None:
        self._store = store
        self._port = port
        self._tasks = tasks

    @property
    def task_type(self) -> str:
        return CANCEL_TASK_TYPE

    def handle(self, payload: dict | None) -> TaskOutcome:
        if not payload:
            logger.error("acli_cancel_task empty payload")
            return Complete()
        return asyncio.run(self._step(payload))

    async def _step(self, raw: Mapping[str, Any]) -> TaskOutcome:
        payload = OperationPayload.from_dict(raw)
        record = await asyncio.to_thread(self._store.get, payload.operation_id)
        if record is None:
            return _unrecorded(payload)
        target = await asyncio.to_thread(self._store.get, str(payload.target_operation_id))
        if target is None:
            return Fail(task_error("not_active", "the operation to cancel was not found"))

        if record.engine_operation_id is None:
            if target.engine_operation_id is None:
                # Still granting: the operation has not reached the engine yet.
                task = await asyncio.to_thread(self._tasks.get_by_id, target.task_id)
                if task is None or task.status in TERMINAL_STATUSES:
                    return Fail(task_error(
                        "not_active", "the operation ended before it reached the engine"
                    ))
                return Reschedule(POLL_SECONDS)
            try:
                op = await self._port.cancel(
                    payload.runtime, action=payload.action, name=payload.cli_code
                )
            except AcliPortError as error:
                if error.code in _TRANSIENT_CODES:
                    return Reschedule(POLL_SECONDS)
                if error.code == "not_active":
                    return Fail(task_error("not_active", "the operation had already ended"))
                return Fail(task_error(error.code, error.message))
            if op.id != target.engine_operation_id:
                return Fail(task_error("not_active", "the operation had already ended"))
            await asyncio.to_thread(
                self._store.set_engine_operation_id, payload.operation_id, op.id
            )
            logger.info(
                "acli_cancel_forwarded operation_id=%s target=%s",
                payload.operation_id, target.operation_id,
            )
        else:
            try:
                op = await self._port.get_operation(payload.runtime, record.engine_operation_id)
            except AcliPortError as error:
                if error.code in _TRANSIENT_CODES:
                    return Reschedule(POLL_SECONDS)
                return Fail(task_error(error.code, error.message))
            if op is None:
                return Fail(task_error(
                    "operation_lost", "the engine no longer has a record of the operation"
                ))
        if op.active:
            return Reschedule(POLL_SECONDS)
        if op.state == EngineState.CANCELLED:
            return Complete()
        if op.state == EngineState.SUCCEEDED:
            return Fail(task_error("not_active", "the operation finished before it was cancelled"))
        return Fail(task_error(op.error_code, op.error_message))


class AcliTaskLifecycle(LifecycleBase):
    """Registers both handlers at boot.

    ``wake_on_enqueue=True``: a caller is usually waiting on the answer, sync
    for up to 30 s, so an idle poll interval would be pure latency.
    """

    def __init__(
        self,
        *,
        registry: HandlerRegistry,
        operation_handler: AcliOperationTaskHandler,
        cancel_handler: AcliCancelTaskHandler,
    ) -> None:
        self._registry = registry
        self._handlers = (operation_handler, cancel_handler)

    async def bootstrap(self) -> None:
        for handler in self._handlers:
            self._registry.register(handler, wake_on_enqueue=True)
            logger.info("[acli_clis] registered %s", handler.task_type)


__all__ = [
    "AcliCancelTaskHandler",
    "AcliOperationTaskHandler",
    "AcliTaskLifecycle",
    "RECORD_GRACE_SECONDS",
]
