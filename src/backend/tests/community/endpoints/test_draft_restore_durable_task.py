"""Durable draft-restore task integration tests using the real SQLite graph."""
import json

import pytest

from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationKind,
    PublishOperationModel,
    PublishOperationState,
)
from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
    DRAFT_RESTORE_TASK,
    PublishTaskLifecycle,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.core.task_queue.repository.protocol import (
    TaskQueueRepositoryProtocol,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.services.worker import TaskWorker
from agentclaw.community.core.task_queue.types import TaskStatus
from agentclaw.community.plugin_api.database import DatabasePlugin
from .test_service_bot_draft_restore import (
    _DRAFT_ID,
    _OWNER,
    _STATUS_OPERATION_ID,
    _seed_completed_restore_operation,
    _seed_restoreable_draft,
)

pytestmark = pytest.mark.integration


async def _run_draft_restore_task(world, operation_id: int):
    injector = world.injector
    registry = injector.get(HandlerRegistry)
    if registry.get(DRAFT_RESTORE_TASK) is None:
        await injector.get(PublishTaskLifecycle).bootstrap()
    task, _created = injector.get(TaskQueueService).enqueue(
        DRAFT_RESTORE_TASK,
        {
            "draft_publish_id": _DRAFT_ID,
            "operation_id": operation_id,
            "operator": _OWNER,
        },
        deadline_seconds=60,
    )
    await injector.get(TaskWorker).run_once()
    return injector.get(TaskQueueRepositoryProtocol).get_by_id(task.id)


@pytest.mark.asyncio
async def test_completed_draft_restore_operation_completes_redelivered_task(
    app_with_testing_modules, world,
):
    _seed_completed_restore_operation(world)

    task = await _run_draft_restore_task(world, _STATUS_OPERATION_ID)

    assert task.status == TaskStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_failed_draft_restore_operation_fails_task(
    app_with_testing_modules, world,
):
    _seed_restoreable_draft(world)
    db = world.get(DatabasePlugin)
    with db.orm_session() as session:
        session.add(PublishOperationModel(
            id=_STATUS_OPERATION_ID,
            publish_id=_DRAFT_ID,
            operation_kind=PublishOperationKind.DRAFT_RESTORE.value,
            stage=PublishStage.DRAFT.value,
            attempt=1,
            state=PublishOperationState.FAILED.value,
            request_id="pub_9102_draft_restore_draft_a1",
            operator=_OWNER,
            bot_uuid="BOT-draft-restore",
            params=json.dumps({
                "source_publish_id": 9101,
                "source_version": 1,
            }),
            last_error="restore failed",
            env="dev",
        ))
        session.flush()

    task = await _run_draft_restore_task(world, _STATUS_OPERATION_ID)

    assert task.status == TaskStatus.FAILED
    assert "restore failed" in task.last_error
