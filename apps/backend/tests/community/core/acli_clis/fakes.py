"""In-memory stand-ins for what ``AcliCliService`` and its task handlers touch.

The engine fake models the container: what acli installed and the one
operation slot, with operations that end only when a test (or ``autorun``)
says so. The queue fake models keyed enqueue, ``get_by_id`` and the worker's
outcome handling, so a test can run tasks to their end.
"""
from __future__ import annotations

import asyncio
import itertools
from dataclasses import replace
from datetime import datetime
from typing import Any

from agentclaw.community.core.acli_clis.operations import (
    AcliAction,
    AcliRuntime,
    EngineOperation,
    EngineState,
)
from agentclaw.community.core.acli_clis.port import AcliMarketPage, AcliPortError, AcliTool
from agentclaw.community.core.acli_clis.repository.models import AcliOperationRecord
from agentclaw.community.core.acli_clis.tasks import (
    AcliCancelTaskHandler,
    AcliOperationTaskHandler,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.caller_identity.models import McpCallType
from agentclaw.community.core.task_queue.types import (
    TERMINAL_STATUSES,
    Complete,
    EnqueueResult,
    Fail,
    Reschedule,
    TaskRecord,
    TaskStatus,
)

OWNER = "owner-1"


def bot(**overrides: Any) -> dict[str, Any]:
    record = {
        "id": 7,
        "bot_id": "bot-1",
        "owner_id": OWNER,
        "entity_id": OWNER,
        "bot_type": "service",
        "status": "ACTIVE",
        "active_engine": "openclaw",
        "template_type": None,
    }
    record.update(overrides)
    return record


class Bots:
    def __init__(self, record: dict[str, Any] | None) -> None:
        self.bot = record

    def get_by_id_and_entity(self, bot_id: str, entity_id: str):
        return self.bot

    def get_unique_by_id(self, bot_id: str):
        return self.bot


class Locks:
    def __init__(self, lock: Any = None) -> None:
        self.lock = lock

    def get_by_key(self, key: str):
        return self.lock


class Identities:
    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self.overrides = dict(overrides or {})

    def list_draft_cli_call_types(self, bot_pk: int, engine_type: str):
        return {code: McpCallType(mode) for code, mode in self.overrides.items()}


class AgentPass:
    """The bot's grant list, written through ``add_cli``."""

    def __init__(self, codes: list[str]) -> None:
        self.items = [
            {"cli_code": c, "cli_name": c, "cli_desc": f"{c} desc", "identity_mode": "owner"}
            for c in codes
        ]
        self.add_error: Exception | None = None
        #: A Passport that accepts a write and does not keep it.
        self.drops_writes = False

    def current_passport_cli_items(self, *, bot):
        return [dict(item) for item in self.items]

    def add_cli(self, *, bot, item):
        if self.add_error:
            raise self.add_error
        if not self.drops_writes:
            self.items.append(dict(item))

    @property
    def codes(self) -> list[str]:
        return [i["cli_code"] for i in self.items]


class Manifest:
    def required_cli_items(self, engine_type, template_type):
        return [{"cli_code": "dataphin"}] if engine_type == "openclaw" else []


class Market:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.tools = (
            AcliTool("dataphin", "dataphin", "d", "1.4.14"),
            AcliTool("deepinsight-cli", "di", "DeepInsight", "1.0.9"),
            AcliTool("yuque-cli", "yuque", "Yuque", "2.0.0"),
        )
        self.error: Exception | None = None

    def search(self, *, keyword, page_no, page_size):
        self.calls.append((keyword, page_no, page_size))
        if self.error:
            raise self.error
        matches = tuple(t for t in self.tools if keyword in t.name or keyword in t.command)
        return AcliMarketPage(matches, len(matches), False)


class Collaborators:
    def __init__(self, levels: dict[str, PermissionLevel] | None = None) -> None:
        self.levels = levels or {}

    def get_operable_permission_level(self, *, bot, user_id, env=None):
        return self.levels.get(user_id, PermissionLevel.NONE)


class Engine:
    """One container per stage: its installed tools and its operation slot."""

    def __init__(self, installed: set[str] | None = None) -> None:
        self.installed: dict[str, set[str]] = {"draft": set(installed or ())}
        self.ops: dict[str, EngineOperation] = {}
        self.active: dict[str, str] = {}
        self.op_stage: dict[str, str] = {}
        self.calls: list[tuple] = []
        #: The ``AcliRuntime.engine`` of every call, in order.
        self.engines: list[str] = []
        #: When set, a submitted operation ends at once with this outcome.
        self.autorun: str | None = "succeeded"
        self.errors: dict[str, list[AcliPortError]] = {}
        self._ids = (f"{n:032x}" for n in itertools.count(1))

    def _raise(self, call: str) -> None:
        queue = self.errors.get(call)
        if queue:
            raise queue.pop(0)

    async def submit(self, runtime: AcliRuntime, *, action: str, name: str) -> EngineOperation:
        self.engines.append(runtime.engine)
        self.calls.append(("submit", runtime.stage, action, name))
        self._raise("submit")
        active = self.active.get(runtime.stage)
        if active is not None:
            raise AcliPortError("engine_busy", "busy", operation=self.ops[active])
        op = EngineOperation(
            id=next(self._ids), action=AcliAction(action), name=name, state=EngineState.RUNNING
        )
        self.ops[op.id] = op
        self.active[runtime.stage] = op.id
        self.op_stage[op.id] = runtime.stage
        if self.autorun:
            self.finish(op.id, self.autorun)
        return self.ops[op.id]

    async def get_operation(self, runtime: AcliRuntime, operation_id: str):
        self.engines.append(runtime.engine)
        self.calls.append(("get", runtime.stage, operation_id))
        self._raise("get")
        return self.ops.get(operation_id)

    async def cancel(self, runtime: AcliRuntime, *, action: str, name: str) -> EngineOperation:
        self.engines.append(runtime.engine)
        self.calls.append(("cancel", runtime.stage, action, name))
        self._raise("cancel")
        active = self.active.get(runtime.stage)
        op = self.ops.get(active) if active else None
        if op is None or op.action != action or op.name != name:
            raise AcliPortError("not_active", "nothing to cancel")
        self.ops[op.id] = replace(op, state=EngineState.CANCELLING)
        return self.ops[op.id]

    async def list_tools(self, runtime: AcliRuntime) -> list[AcliTool]:
        self.engines.append(runtime.engine)
        self.calls.append(("list", runtime.stage))
        self._raise("list")
        return [
            AcliTool(name, name[:2], f"{name} engine desc", "1.0.0")
            for name in sorted(self.installed.get(runtime.stage, set()))
        ]

    def finish(self, op_id: str, state: str) -> None:
        """End an operation as the engine would: change the tools, free the slot."""
        op = self.ops[op_id]
        stage = self.op_stage[op_id]
        tools = self.installed.setdefault(stage, set())
        result = error = None
        if state == "succeeded":
            if op.action == "install":
                result = {"name": op.name, "command": op.name[:2], "version": "1.0.0",
                          "description": "", "already_installed": op.name in tools}
                tools.add(op.name)
            else:
                result = {"name": op.name, "uninstalled": op.name in tools}
                tools.discard(op.name)
        elif state == "failed":
            error = {"code": f"{op.action}_failed", "message": "acli said no"}
        self.ops[op_id] = replace(op, state=EngineState(state), result=result, error=error)
        if self.active.get(stage) == op_id:
            del self.active[stage]


class Store:
    def __init__(self) -> None:
        self.rows: dict[str, AcliOperationRecord] = {}
        self.order: list[str] = []

    def create(self, record: AcliOperationRecord) -> None:
        assert record.operation_id not in self.rows
        self.rows[record.operation_id] = record
        self.order.append(record.operation_id)

    def get(self, operation_id: str):
        return self.rows.get(operation_id)

    def latest(self, *, bot_pk: int, stage: str, kind: str):
        for op_id in reversed(self.order):
            row = self.rows[op_id]
            if (row.bot_pk, row.stage, row.kind) == (bot_pk, stage, kind):
                return row
        return None

    def set_engine_operation_id(self, operation_id: str, engine_operation_id: str) -> None:
        self.rows[operation_id] = replace(
            self.rows[operation_id], engine_operation_id=engine_operation_id
        )


class Queue:
    """``TaskQueueService.enqueue`` + ``get_by_id``, and a worker that applies outcomes."""

    def __init__(self) -> None:
        self.tasks: dict[int, TaskRecord] = {}
        self._ids = itertools.count(100)

    def enqueue(self, task_type, payload, deadline_seconds, *, delay_seconds=0,
                idempotency_key=None):
        for task in self.tasks.values():
            if (task.task_type == task_type and task.idempotency_key == idempotency_key
                    and idempotency_key is not None and task.status not in TERMINAL_STATUSES):
                return EnqueueResult(task, False)
        task = TaskRecord(
            id=next(self._ids), task_type=task_type, payload=dict(payload),
            status=TaskStatus.PENDING, deadline_at=datetime.now(), run_at=None,
            claimed_by=None, lease_expires_at=None, attempts=0, last_error=None,
            env="test", app="agentclaw", idempotency_key=idempotency_key,
        )
        self.tasks[task.id] = task
        return EnqueueResult(task, True)

    def get_by_id(self, task_id: int):
        return self.tasks.get(task_id)

    def live(self) -> list[TaskRecord]:
        return [t for t in self.tasks.values() if t.status not in TERMINAL_STATUSES]

    def apply(self, task: TaskRecord, outcome) -> None:
        if isinstance(outcome, Complete):
            status, error = TaskStatus.SUCCEEDED, task.last_error
        elif isinstance(outcome, Fail):
            status, error = TaskStatus.FAILED, outcome.error
        elif isinstance(outcome, Reschedule):
            status, error = TaskStatus.PENDING, task.last_error
        else:
            raise AssertionError(f"unexpected outcome {outcome!r}")
        self.tasks[task.id] = replace(task, status=status, last_error=error)

    def time_out(self, task_id: int) -> None:
        self.tasks[task_id] = replace(self.tasks[task_id], status=TaskStatus.TIMED_OUT)


class Worker:
    """Runs every live task's next step, as ``TaskWorker`` would."""

    def __init__(self, queue: Queue, *handlers) -> None:
        self.queue = queue
        self.handlers = {h.task_type: h for h in handlers}

    async def step(self) -> int:
        ran = 0
        for task in self.queue.live():
            outcome = await self.handlers[task.task_type]._step(task.payload)
            self.queue.apply(self.queue.tasks[task.id], outcome)
            ran += 1
        return ran

    async def drain(self, *, rounds: int = 50) -> None:
        for _ in range(rounds):
            if not await self.step():
                return
            await asyncio.sleep(0)

    async def run_while(self, waiter, *, rounds: int = 200):
        """Step the tasks until ``waiter`` (a pending service call) returns."""
        call = asyncio.ensure_future(waiter)
        for _ in range(rounds):
            if call.done():
                break
            await self.step()
            await asyncio.sleep(0.01)
        return await call


def handlers(*, store, engine, bots, agentpass, queue):
    return (
        AcliOperationTaskHandler(store=store, port=engine, bots=bots, reconciler=agentpass),
        AcliCancelTaskHandler(store=store, port=engine, tasks=queue),
    )


__all__ = [
    "OWNER",
    "AgentPass",
    "Bots",
    "Collaborators",
    "Engine",
    "Identities",
    "Locks",
    "Manifest",
    "Market",
    "Queue",
    "Store",
    "Worker",
    "bot",
    "handlers",
]
