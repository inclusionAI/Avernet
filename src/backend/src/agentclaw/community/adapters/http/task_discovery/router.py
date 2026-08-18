"""task_discovery 公开路由 — 手动触发任务发现。

参考 ``cron_noauth_router`` 的模式：无需认证的公开端点，
供 CLI 或外部调度器通过 HTTP 触发。

端点:
  POST /api/public/task-discovery/discover          手动触发任务发现
  GET  /api/public/task-discovery/status             查看任务状态

任务执行不在本模块负责 — 由 task 目录下另外的执行框架处理。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Query

from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryService,
    create_default_service,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    SqliteTaskReader,
)
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/public/task-discovery", tags=["task-discovery"])

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


@router.post("/discover")
async def discover_tasks(
    user_id: str = Query("default", description="用户 ID"),
    agent_id: str = Query("bot_001", description="Bot/Agent ID"),
) -> dict:
    """手动触发任务发现流程。

    读取 mock 数据中的待确认任务，为每个任务创建 engine session + session_url。
    """
    logger.info(
        "[task_discovery] discover triggered: user_id=%s, agent_id=%s",
        user_id, agent_id,
    )
    try:
        service = _build_service()
        results = await service.discover(user_id=user_id, agent_id=agent_id)

        return {
            "success": True,
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
        }
    except Exception as exc:
        logger.error("[task_discovery] discover failed: %s", exc, exc_info=True)
        return {"success": False, "message": str(exc)}


@router.get("/status")
async def get_status() -> dict:
    """查看任务发现状态(从 SQLite db 读取)。"""
    db_path = _resolve_db_path()
    try:
        reader = SqliteTaskReader(db_path)
        tasks = reader.read_discovered_tasks()
    except Exception as exc:
        return {"success": False, "message": str(exc)}

    return {
        "success": True,
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
    }
