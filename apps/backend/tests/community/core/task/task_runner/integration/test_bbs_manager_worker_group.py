from __future__ import annotations

import asyncio
from agentclaw.community.core.task.task_runner.modal_executor.task_executor_bbs import (
    TaskExecutorBbsMixin,
)


class _GroupTaskExecutor(TaskExecutorBbsMixin):
    def __init__(self) -> None:
        self.formations = []

    async def form_coop_group(self, formation) -> str:
        self.formations.append(formation)
        return "group-1"

    async def get_group_session(self, group_id: str) -> str | None:
        self.group_id = group_id
        return "sess-group"


def test_bbs_manager_worker_group_carries_relay_token() -> None:
    """BBS Relay must not lose its run-generation token when forming a group."""
    executor = _GroupTaskExecutor()

    result = asyncio.run(executor._bbs_execute_as_manager_worker_group(
        task_id="task-relay",
        node_id="node-relay",
        winner_bot_id="winner:user",
        owner_user_id="146836",
        task_instruction="relay instruction",
        deadline_monotonic=0,
        relay_execution=True,
        task_objective="relay objective",
        acceptances=[{"id": "a1", "description": "first acceptance"}],
        upstream_outputs={"node-0": {"fact": "upstream"}},
        relay_blackboard={"snapshot": {}},
        relay_token="attempt-1",
    ))

    assert result == {
        "group_id": "group-1",
        "session_id": "sess-group",
        "success": True,
    }
    assert len(executor.formations) == 1
    formation = executor.formations[0]
    assert formation.bot_ids == ["winner:user"]
    assert formation.members_info == [
        {"bot_id": "winner:user", "role": "manager"}
    ]
    assert formation.extend_props["manager_bot_id"] == "winner:user"
    assert formation.extend_props["relay_execution"] is True
    assert formation.extend_props["relay_token"] == "attempt-1"
