"""task_discovery 公开路由 — 手动触发任务发现。

参考 ``cron_noauth_router`` 的模式：无需认证的公开端点，
供 CLI 或外部调度器通过 HTTP 触发。

端点:
  POST /api/public/task-discovery/discover          手动触发任务发现
  GET  /api/public/task-discovery/status             查看任务状态

任务执行不在本模块负责 — 由 task 目录下另外的执行框架处理。
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryService,
    create_default_service,
)
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/public/task-discovery", tags=["task-discovery"])


def _build_service() -> DiscoveryService:
    """从环境变量构建 DiscoveryService。"""
    import os
    from pathlib import Path

    project_root = Path(__file__).resolve()
    # adapters/http/task_discovery/router.py → 9 级上溯到项目根
    for _ in range(9):
        project_root = project_root.parent

    data_file = os.environ.get(
        "TASK_DISCOVERY_DATA_FILE",
        str(project_root / "scripts" / "data" / "discovered_tasks.json"),
    )
    engine_url = os.environ.get(
        "TASK_DISCOVERY_ENGINE_URL", "http://localhost:20003"
    )
    frontend_url = os.environ.get(
        "TASK_DISCOVERY_FRONTEND_URL", "http://localhost:8000"
    )
    return create_default_service(
        data_file=data_file,
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
    """查看任务发现状态。"""
    import json
    import os
    from pathlib import Path

    project_root = Path(__file__).resolve()
    for _ in range(9):
        project_root = project_root.parent

    data_file = os.environ.get(
        "TASK_DISCOVERY_DATA_FILE",
        str(project_root / "scripts" / "data" / "discovered_tasks.json"),
    )

    try:
        with open(data_file, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return {"success": False, "message": str(exc)}

    tasks = data.get("tasks", [])
    return {
        "success": True,
        "total": len(tasks),
        "tasks": [
            {
                "task_id": t.get("task_id"),
                "project_name": t.get("project_name"),
                "status": t.get("status", "unknown"),
                "priority": t.get("priority", "medium"),
            }
            for t in tasks
        ],
    }