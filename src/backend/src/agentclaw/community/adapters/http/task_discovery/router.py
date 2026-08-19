"""task_discovery 公开路由 — 定时触发、手动触发、状态查询、DreamMode 开关。

参考 ``cron_noauth_router`` 的模式：通过 ``Injected()`` 注入依赖。

端点:
  POST /api/public/task-discovery/scheduled-trigger   外部 scheduler 定时触发
  POST /api/public/task-discovery/discover            手动触发任务发现
  POST /api/public/task-discovery/dream-mode          开启/关闭 DreamMode
  GET  /api/public/task-discovery/status              查看任务发现状态
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Query

from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryService,
)
from agentclaw.community.core.task.task_discovery.protocols import (
    BotServiceProtocol,
)
from agentclaw.community.core.task.task_discovery.scheduler import (
    TaskDiscoveryScheduler,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    SqliteTaskReader,
)
from agentclaw.community.di import Injected
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


# ── 定时调度触发（外部 scheduler 调用）──────────────────────


@router.post("/scheduled-trigger")
async def scheduled_trigger(
    service: DiscoveryService = Injected(DiscoveryService),
) -> dict:
    """外部 scheduler 定时触发的入口端点。

    供 system cron / K8s CronJob 等外部调度器调用。
    backend 进程内不运行任何 asyncio 定时器时，用此端点触发。
    """
    logger.info("[task_discovery] scheduled-trigger received")
    try:
        results = await service.discover_all_bots()
        return {
            "success": True,
            "total_discovered": sum(1 for r in results if r.success),
            "results": [
                {
                    "task_id": r.task.task_id,
                    "bot_id": r.task.bot_id,
                    "success": r.success,
                    "session_id": r.session.session_id if r.session else None,
                    "session_url": r.session.session_url if r.session else None,
                    "notification_sent": r.notification_sent,
                    "error": r.error,
                }
                for r in results
            ],
        }
    except Exception as exc:
        logger.error("[task_discovery] scheduled-trigger failed: %s", exc, exc_info=True)
        return {"success": False, "message": str(exc)}


# ── 手动触发 ───────────────────────────────────────────────


@router.post("/discover")
async def discover_tasks(
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: str = Query(..., description="Bot 所有者 ID"),
    agent_id: str = Query(..., description="Bot/Agent ID"),
    model: str | None = Query(None, description="可选模型覆盖"),
    service: DiscoveryService = Injected(DiscoveryService),
) -> dict:
    """手动触发任务发现 — 直接调用 discover，不经过 cron round-trip。

    读取 mock 数据中的待确认任务，创建 engine session + WebSocket 注入消息 + 投递通知。
    """
    logger.info(
        "[task_discovery] discover triggered: bot_id=%s, owner_id=%s",
        bot_id, owner_id,
    )
    try:
        results = await service.discover(
            bot_id=bot_id,
            owner_id=owner_id,
            agent_id=agent_id,
            model=model,
        )

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
                    "notification_sent": r.notification_sent,
                    "error": r.error,
                }
                for r in results
            ],
        }
    except Exception as exc:
        logger.error("[task_discovery] discover failed: %s", exc, exc_info=True)
        return {"success": False, "message": str(exc)}


# ── DreamMode 开关 ──────────────────────────────────────────


@router.post("/dream-mode")
async def toggle_dream_mode(
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: str = Query(..., description="Bot 所有者 ID"),
    enabled: bool = Query(True, description="true=开启, false=关闭"),
    scheduler: TaskDiscoveryScheduler = Injected(TaskDiscoveryScheduler),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> dict:
    """开启/关闭任务发现 DreamMode。

    开启 → 确保 scheduler 运行
    关闭 → 停止调度
    权限：只能为自己的 bot 操作（通过 BotService.get_bot 校验）
    """
    # 权限校验
    try:
        bot = bot_service.get_bot(bot_id, owner_id)
        if not bot:
            return {"success": False, "message": "bot not found or not owned by user"}
    except Exception as exc:
        logger.error("[task_discovery] dream-mode ownership check failed: %s", exc)
        return {"success": False, "message": str(exc)}

    if enabled:
        scheduler.enable_for_bot(bot_id, owner_id)
    else:
        scheduler.disable_for_bot(bot_id, owner_id)

    logger.info(
        "[task_discovery] dream-mode: bot=%s enabled=%s",
        bot_id, enabled,
    )
    return {"success": True, "enabled": enabled, "bot_id": bot_id}


# ── 调度器状态 ──────────────────────────────────────────────


@router.get("/scheduler-status")
async def get_scheduler_status(
    scheduler: TaskDiscoveryScheduler = Injected(TaskDiscoveryScheduler),
) -> dict:
    """查看 APScheduler 调度状态 — cron 表达式、下次触发时间、是否运行。"""
    return {"success": True, **scheduler.get_status()}


# ── 状态查询 ───────────────────────────────────────────────


@router.get("/status")
async def get_status(
    bot_id: str | None = Query(None, description="按 bot_id 过滤"),
    owner_id: str | None = Query(None, description="按 owner_id 过滤"),
) -> dict:
    """查看任务发现状态(从 SQLite db 读取)。

    支持按 bot_id/owner_id 过滤；不传则返回全量。
    """
    db_path = _resolve_db_path()
    try:
        reader = SqliteTaskReader(db_path)
        tasks = reader.read_discovered_tasks()
    except Exception as exc:
        return {"success": False, "message": str(exc)}

    # 按参数过滤
    if bot_id:
        tasks = [t for t in tasks if t.bot_id == bot_id]
    if owner_id:
        tasks = [t for t in tasks if t.owner_id == owner_id]

    return {
        "success": True,
        "total": len(tasks),
        "tasks": [
            {
                "task_id": t.task_id,
                "bot_id": t.bot_id,
                "owner_id": t.owner_id,
                "dt": t.dt,
                "project_name": t.project_name,
                "status": t.status,
                "priority": t.priority,
            }
            for t in tasks
        ],
    }