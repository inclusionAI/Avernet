"""task-discovery HTTP adapter —— 手动触发任务发现(归属任务模块 openapi 公开面)。

与 task router 同包(``openapi_v1/task/``),统一 ``/openapi_v1`` 返回协议。端点:

  POST /openapi/v1/collaboration/tasks/discovery/discover   手动触发任务发现
  GET  /openapi/v1/collaboration/tasks/discovery/status      查看任务发现状态

成功经 ``envelope()`` → ``Envelope{code,message,data,request_id}``;失败上抛
``InternalError`` → 经 ``@envelope_errors`` 重抛到中央 ``DomainError`` handler →
500 ``ErrorEnvelope``。与 dev 进化后的 DiscoveryService 对齐:经 backend connection
API 定位 per-bot engine 创建 session,经 ``NotifySenderPlugin`` 投递通知。
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
    create_default_service,
)
from agentclaw.community.core.task.task_discovery.session_creator import (
    HttpSessionCreator,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    SqliteTaskReader,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import NotifySenderPlugin

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


def _build_service(notify_sender: NotifySenderPlugin) -> DiscoveryService:
    """构建 DiscoveryService(经 backend connection API 定位 per-bot engine)。"""
    return create_default_service(
        data_file=_resolve_db_path(),
        notify_sender=notify_sender,
        session_creator=HttpSessionCreator(),
    )


@router.post("/discover", response_model=Envelope[dict])
@envelope_errors
async def discover_tasks(
    request: Request,
    user_id: str = Query("default", description="用户 ID"),
    agent_id: str = Query("bot_001", description="Bot/Agent ID"),
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: str = Query(..., description="Bot 所有者 ID"),
    notify_sender: NotifySenderPlugin = Injected(NotifySenderPlugin),  # noqa: B008
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
        service = _build_service(notify_sender=notify_sender)
        results = await service.discover(
            user_id=user_id, agent_id=agent_id, bot_id=bot_id, owner_id=owner_id,
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
