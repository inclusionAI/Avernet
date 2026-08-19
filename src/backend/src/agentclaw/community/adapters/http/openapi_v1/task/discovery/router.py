"""task_discovery 公开路由 — 手动触发任务发现。

参考 ``cron_noauth_router`` 的模式：无需认证的公开端点，
供 CLI 或外部调度器通过 HTTP 触发。

端点:
  POST /openapi/v1/task/discovery/discover          手动触发任务发现
  GET  /openapi/v1/task/discovery/status             查看任务状态

任务执行不在本模块负责 — 由 task 目录下另外的执行框架处理。
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
from agentclaw.community.core.task.task_discovery.task_reader import (
    SqliteTaskReader,
)
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/openapi/v1/task/discovery", tags=["task-discovery"])

#: 默认 db 文件路径(9 级上溯到项目根 → scripts/.dependencies/data/discovered_tasks.db)
_PROJECT_ROOT = Path(__file__).resolve()
for _ in range(9):
    _PROJECT_ROOT = _PROJECT_ROOT.parent
_DEFAULT_DB = str(_PROJECT_ROOT / "scripts" / ".dependencies" / "data" / "discovered_tasks.db")


def _resolve_db_path() -> str:
    """从环境变量或默认路径解析 db 文件路径。"""
    return os.environ.get("TASK_DISCOVERY_DATA_FILE", _DEFAULT_DB)


def _build_service() -> DiscoveryService:
    """从环境变量构建 DiscoveryService。"""
    engine_url = os.environ.get(
        "TASK_DISCOVERY_ENGINE_URL", "http://localhost:20003"
    )
    frontend_url = os.environ.get(
        "TASK_DISCOVERY_FRONTEND_URL", "http://localhost:8000"
    )
    return create_default_service(
        data_file=_resolve_db_path(),
        engine_base_url=engine_url,
        engine_frontend_url=frontend_url,
    )


@router.post("/discover", response_model=Envelope[dict])
@envelope_errors
async def discover_tasks(
    request: Request,
    user_id: str = Query("default", description="用户 ID"),
    agent_id: str = Query("bot_001", description="Bot/Agent ID"),
) -> Envelope[dict]:
    """手动触发任务发现流程。

    读取 mock 数据中的待确认任务，为每个任务创建 engine session + session_url。统一信封
    ``Envelope{code,data:{discovered,tasks},...}``;失败的 build/discover 经 ``@envelope_errors``
    上抛 → 中央 handler → ``ErrorEnvelope``(非 200 内吞)。
    """
    logger.info(
        "[task_discovery] discover triggered: user_id=%s, agent_id=%s",
        user_id, agent_id,
    )
    try:
        service = _build_service()
        results = await service.discover(user_id=user_id, agent_id=agent_id)
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
                    "session_url": r.session.session_url if r.session else None,
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
    """查看任务发现状态(从 SQLite db 读取)。统一信封 ``Envelope{code,data:{total,tasks},...}``;
    db 读失败 → ``InternalError`` 经 ``@envelope_errors`` 映射 500 → ``ErrorEnvelope``。"""
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
