"""task HTTP 内部适配层:callback-report + bbs relay + task_loop inbound PUSH callback router。

内部 API(前缀 ``/api/v1/collaboration/tasks``),不经 gateway spanner。前端公开面
(execute/dashboard/list)见 ``adapters/http/openapi_v1/task/``。
"""
from fastapi import APIRouter

from agentclaw.community.adapters.http.task.router import (
    router as _legacy_task_internal_router,
    task_callback_router,
)
from agentclaw.community.adapters.http.task.trajectory_replay_router import (
    router as _trajectory_replay_router,
)

task_internal_router = APIRouter()
task_internal_router.include_router(_legacy_task_internal_router)
task_internal_router.include_router(_trajectory_replay_router)

__all__ = ["task_internal_router", "task_callback_router"]
