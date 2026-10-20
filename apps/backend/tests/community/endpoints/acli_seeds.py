"""Seeds shared by the acli CLI endpoint cases: an operation already running."""
from __future__ import annotations

import time
import uuid

from agentclaw.community.core.acli_clis.operations import (
    KIND_OPERATION,
    OPERATION_TASK_DEADLINE_SECONDS,
    OPERATION_TASK_TYPE,
    OperationPayload,
    operation_key,
)
from agentclaw.community.core.acli_clis.repository.models import AcliOperationRecord
from agentclaw.community.core.acli_clis.repository.store import AcliOperationStore
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService


def seed_running_operation(
    world, *, bot: dict, action: str, cli_code: str, operation_id: str | None = None
) -> str:
    """Enqueue an add or remove the engine has accepted but not finished; returns its id."""
    payload = OperationPayload(
        operation_id=operation_id or uuid.uuid4().hex, kind=KIND_OPERATION, action=action,
        cli_code=cli_code, bot_pk=int(bot["id"]), bot_id=str(bot["bot_id"]),
        entity_id=str(bot["owner_id"]), owner_id=str(bot["owner_id"]), stage="draft",
        actor_id=str(bot["owner_id"]), enqueued_at=time.time(),
    )
    result = world.get(TaskQueueService).enqueue(
        OPERATION_TASK_TYPE, payload.to_dict(), OPERATION_TASK_DEADLINE_SECONDS,
        idempotency_key=operation_key(payload.bot_pk, payload.stage),
        delay_seconds=3600,
    )
    world.get(AcliOperationStore).create(AcliOperationRecord(
        operation_id=payload.operation_id, task_id=result.record.id, kind=KIND_OPERATION,
        action=action, cli_code=cli_code, bot_pk=payload.bot_pk, stage="draft",
        engine_operation_id="e" * 32,
    ))
    return payload.operation_id
