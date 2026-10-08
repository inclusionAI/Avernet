"""Public HTTP handler for read-only task trajectory replay.

The owning task router registers this handler so its ``PublicAPIRoute`` applies the
full prefixed path when consulting the authorization inventory.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import Principal, require_principal
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.adapters.http.task.trajectory_replay_schemas import (
    TaskTrajectoryReplayDTO,
    trajectory_replay_to_dto,
)
from agentclaw.community.api.task.task_context_service import TaskContextServiceProtocol
from agentclaw.community.di import Injected

PrincipalDep = Annotated[Principal, Depends(require_principal)]


@envelope_errors
async def replay_task_trajectory(
    task_id: Annotated[str, Query(description="任务 ID")],
    request: Request,
    principal: PrincipalDep,
    playback_rate: Annotated[float, Query(ge=0.1, le=10.0)] = 1.0,
    from_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
    service: TaskContextServiceProtocol = Injected(TaskContextServiceProtocol),  # noqa: B008
) -> Envelope[TaskTrajectoryReplayDTO]:
    """按原始事件时序返回播放帧；不会重新执行 bot、工具或任务状态变更。"""
    del principal
    replay = await service.replay_trajectory(
        task_id,
        playback_rate=playback_rate,
        from_sequence=from_sequence,
        limit=limit,
    )
    return envelope(trajectory_replay_to_dto(replay), request)
