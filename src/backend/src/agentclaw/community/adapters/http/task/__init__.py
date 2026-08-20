"""task HTTP 内部适配层:callback-report + bbs relay + task_loop inbound PUSH callback router。

内部 API(前缀 ``/api/v1/collaboration/tasks``),不经 gateway spanner。前端公开面
(execute/dashboard/list)见 ``adapters/http/openapi_v1/task/``。
"""
from agentclaw.community.adapters.http.task.router import (
    router as task_internal_router,
    task_callback_router,
)

__all__ = ["task_internal_router", "task_callback_router"]
