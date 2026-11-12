"""Local Todo collaboration runtime-refresh behavior."""

from __future__ import annotations

import pytest

from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationContextRequest,
)
from agentclaw.community.plugins.local.todo_collaboration import (
    LocalTodoCollaborationPlugin,
)


def _request() -> TodoCollaborationContextRequest:
    return TodoCollaborationContextRequest(
        space_id="7",
        environment="test",
        principal_id="user_1",
        product_kind="team_todo",
        workflow_kind="team_todo_execution",
        method_kind="Single",
        executor_id="bot_1",
        executor_owner_id="owner_1",
        objective="Write the report",
        todo_id="todo_1",
    )


@pytest.mark.asyncio
async def test_refresh_context_run_completes_only_confirmed_completed_runtime() -> None:
    plugin = LocalTodoCollaborationPlugin()
    created = plugin.create_context(_request())
    started = await plugin.run_context(
        collab_context_id=created.collab_context_id,
        actor_id="user_1",
    )

    running = await plugin.refresh_context_run(
        collab_context_id=started.collab_context_id,
        actor_id="user_1",
    )
    plugin.set_run_status_for_test(
        collab_context_id=started.collab_context_id,
        status="COMPLETED",
    )
    completed = await plugin.refresh_context_run(
        collab_context_id=started.collab_context_id,
        actor_id="user_1",
    )

    assert running.status == "in_progress"
    assert completed.status == "done"
    assert completed.events[-1].event_type == "task.completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["FAILED", "TIME_OUT", "ABORTED"])
async def test_refresh_context_run_does_not_complete_failed_runtime(
    status: str,
) -> None:
    plugin = LocalTodoCollaborationPlugin()
    created = plugin.create_context(_request())
    started = await plugin.run_context(
        collab_context_id=created.collab_context_id,
        actor_id="user_1",
    )
    plugin.set_run_status_for_test(
        collab_context_id=started.collab_context_id,
        status=status,
    )

    refreshed = await plugin.refresh_context_run(
        collab_context_id=started.collab_context_id,
        actor_id="user_1",
    )

    assert refreshed.status == "in_progress"
    assert all(event.event_type != "task.completed" for event in refreshed.events)
