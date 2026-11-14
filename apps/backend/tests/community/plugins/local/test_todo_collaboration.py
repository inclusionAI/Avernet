"""Local Todo collaboration runtime-refresh behavior."""

from __future__ import annotations

import pytest

from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationContextRequest,
    TodoCollaborationEventRequest,
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


@pytest.mark.asyncio
async def test_task_rejected_event_resumes_completed_context() -> None:
    plugin = LocalTodoCollaborationPlugin()
    created = plugin.create_context(_request())
    started = await plugin.run_context(
        collab_context_id=created.collab_context_id,
        actor_id="user_1",
    )
    completed = plugin.complete_context(started.collab_context_id)
    rejected = plugin.append_event(
        collab_context_id=completed.collab_context_id,
        request=TodoCollaborationEventRequest(
            expected_version=completed.context_version,
            event_kind="task.rejected",
            primitive_type="Decision",
            actor_id="user_1",
            actor_type="human",
            parent_event=completed.current_event_id,
            visibility="workspace",
            payload={
                "content": "第二段结论缺少数据来源，请补充证据。",
                "decision_id": "decision_reject_1",
            },
        ),
    )

    resumed = await plugin.resume_context(
        collab_context_id=completed.collab_context_id,
        actor_id="user_1",
        triggered_by_event_id=rejected.event_id,
    )

    assert resumed.status == "in_progress"
    assert resumed.events[-1].event_type == "task.started"
    assert resumed.events[-1].payload["triggered_by_event_id"] == rejected.event_id
    assert resumed.events[-1].payload["resumed"] is True
