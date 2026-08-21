"""task-discovery HTTP adapter —— 手动触发任务发现(归属任务模块 openapi 公开面)。

与 task router 同包(``openapi_v1/task/``),统一 ``/openapi_v1`` 返回协议。端点:

  POST /openapi/v1/collaboration/tasks/discovery/discover   手动触发任务发现
  GET  /openapi/v1/collaboration/tasks/discovery/status      查看任务发现状态

成功经 ``envelope()`` → ``Envelope{code,message,data,request_id}``;失败上抛
``InternalError`` → 经 ``@envelope_errors`` 重抛到中央 ``DomainError`` handler →
500 ``ErrorEnvelope``。

``discover`` 复用 ``TaskDiscoveryModule`` 绑定的 ``DiscoveryService`` 单例 —— 与
``TaskDiscoveryScheduler`` 定时触发的是同一个实例、同一条 reader/initiator/notify
链路,手动与定时两条触发路径因此不会漂移。``status`` 仍按请求新建 reader:db 路径
来自 ``TASK_DISCOVERY_DATA_FILE``,须在请求时读取而非在容器构建时定格。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.core.errors import InternalError
from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryService,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    SqliteTaskReader,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(
    prefix="/openapi/v1/collaboration/tasks/discovery", tags=["task-discovery"]
)

#: 默认 db 文件路径(10 级上溯到仓库根 → scripts/.dependencies/data/discovered_tasks.db)
_PROJECT_ROOT = Path(__file__).resolve()
for _ in range(10):
    _PROJECT_ROOT = _PROJECT_ROOT.parent
_DEFAULT_DB = str(_PROJECT_ROOT / "scripts" / ".dependencies" / "data" / "discovered_tasks.db")


def _resolve_db_path() -> str:
    """从环境变量或默认路径解析 db 文件路径。"""
    return os.environ.get("TASK_DISCOVERY_DATA_FILE", _DEFAULT_DB)


@router.post("/discover", response_model=Envelope[dict])
@envelope_errors
async def discover_tasks(
    request: Request,
    user_id: str = Query("default", description="用户 ID"),
    agent_id: str = Query("bot_001", description="Bot/Agent ID"),
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: str = Query(..., description="Bot 所有者 ID"),
    service: DiscoveryService = Injected(DiscoveryService),  # noqa: B008
) -> Envelope[dict]:
    """手动触发任务发现:读取任务 → 在 per-bot engine 创建 session → 投递通知。

    session-creation 错误按任务捕获(非顶层),故 discover 端到端跑完总返 200
    ``Envelope``(失色任务在 ``tasks[].success/error`` 体现)。仅顶层 build/discover
    失败 → ``InternalError`` → 500 ``ErrorEnvelope``。
    """
    logger.info(
        "[task_discovery] discover triggered: user_id=%s, agent_id=%s, bot_id=%s",
        user_id, agent_id, bot_id,
    )
    try:
        results = await service.discover(
            bot_id=bot_id, owner_id=owner_id, agent_id=agent_id,
        )
    except Exception as exc:
        logger.error("[task_discovery] discover failed: %s", exc, exc_info=True)
        raise InternalError("discovery failed") from exc
    return envelope(
        {
            "discovered": len(results),
            "tasks": [
                {
                    "task_id": r.task.task_id,
                    "project_name": r.task.project_name,
                    "success": r.success,
                    "session_id": r.session.session_id if r.session else None,
                    "notification_sent": r.notification_sent,
                    "error": r.error,
                }
                for r in results
            ],
        },
        request,
    )


@router.get("/status", response_model=Envelope[dict])
@envelope_errors
async def get_status(request: Request) -> Envelope[dict]:
    """查看任务发现状态(从 SQLite db 读取)。db 读失败 → ``InternalError`` → 500。"""
    db_path = _resolve_db_path()
    try:
        reader = SqliteTaskReader(db_path)
        tasks = reader.read_discovered_tasks()
    except Exception as exc:
        raise InternalError("status read failed") from exc
    return envelope(
        {
            "total": len(tasks),
            "tasks": [
                {
                    "task_id": t.task_id,
                    "project_name": t.project_name,
                    "status": t.status,
                    "priority": t.priority,
                }
                for t in tasks
            ],
        },
        request,
    )
