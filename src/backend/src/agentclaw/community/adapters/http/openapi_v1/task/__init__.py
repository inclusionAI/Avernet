"""task HTTP 公开适配层:execute/dashboard/list/bbs-list router(前端公开面,经 gateway spanner)。

其余内部接口(回投 / bbs 接力步 claim·attach·result / 任务发现阶段)见 ``adapters/http/task/``(前缀 ``/api/v1/collaboration/tasks``)。
"""
from agentclaw.community.adapters.http.openapi_v1.task.router import router as task_router

__all__ = ["task_router"]
