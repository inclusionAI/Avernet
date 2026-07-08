"""System health + readiness API.

1. GET /api/system/health/engine  — 逐 Bot 探测 OpenClaw 引擎健康（liveness, legacy）
2. GET /api/system/health/bots    — 逐 Bot 探测沙箱健康（liveness, legacy）
3. GET /api/system/readiness      — 逐 Bot 运行时 readiness 状态（state enum, 供前端展示）
4. POST /api/system/disk-usage    — 触发后台分析 NAS 目录磁盘用量

Rule 14: all per-runtime branching lives in ``HealthProbePlugin`` impls
(``plugins/local/health_probe.py`` and ``plugins/prod/health_probe.py``).
Routes are pure dispatchers; mode is never read here.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from agentclaw.community.adapters.http.system.schemas import BotReadiness, ReadinessResponse
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.access.admin_scopes import disk_usage_admin
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
import agentclaw.community.core.nas_usage  # noqa: F401 — register SQLAlchemy models
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.health_probe import HealthProbePlugin


logger = get_logger()

router = APIRouter(prefix="/api/system/health", tags=["system-health"])


# ── Shared helpers ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═════════════════════════════════════════════════════════════════════════════
# 1. Engine health — 逐 Bot 探测 OpenClaw 引擎
# ═════════════════════════════════════════════════════════════════════════════


@router.get("/engine")
async def engine_health(
    user: AuthenticatedUser = Depends(get_current_user),
    probe: HealthProbePlugin = Injected(HealthProbePlugin),
):
    """逐 Bot OpenClaw 引擎健康检查。"""
    try:
        engines = await probe.engine_health(user.staffId)
    except Exception as e:
        logger.error(f"[health/engine] probe failed: {e}")
        engines = []

    total = len(engines)
    ready_count = sum(
        1
        for e in engines
        if e.get("state") == "ready"
    )

    return {
        "checked_at": _now_iso(),
        "mode": probe.mode_label,
        "total": total,
        "ready_count": ready_count,
        "engines": engines,
    }


# ═════════════════════════════════════════════════════════════════════════════
# 2. Bots health — 逐 Bot 沙箱健康
# ═════════════════════════════════════════════════════════════════════════════


@router.get("/bots")
async def bots_health(
    user: AuthenticatedUser = Depends(get_current_user),
    probe: HealthProbePlugin = Injected(HealthProbePlugin),
):
    """逐 Bot 沙箱健康检查。"""
    try:
        bots = await probe.bots_health(user.staffId)
    except Exception as e:
        logger.error(f"[health/bots] probe failed: {e}")
        bots = []

    total = len(bots)
    healthy_count = sum(
        1
        for b in bots
        if b.get("healthy")
    )

    return {
        "checked_at": _now_iso(),
        "mode": probe.mode_label,
        "total": total,
        "healthy_count": healthy_count,
        "healthy": healthy_count == total and total > 0,
        "bots": bots,
    }


# ═════════════════════════════════════════════════════════════════════════════
# 3. Sandbox health — 探测指定 Bot 对应沙箱
# ═════════════════════════════════════════════════════════════════════════════


@router.get("/sandbox")
async def sandbox_health(
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: str = Query(..., description="Bot Owner ID"),
    user: AuthenticatedUser = Depends(get_current_user),
    probe: HealthProbePlugin = Injected(HealthProbePlugin),
):
    """探测指定 Bot 对应沙箱的健康状态。"""
    logger.info(
        f"[health/sandbox] bot_id={bot_id}, owner_id={owner_id}, user={user.staffId}"
    )
    try:
        return await probe.sandbox_health(bot_id, owner_id)
    except Exception as e:
        logger.error(f"[health/sandbox] probe failed: {e}")
        return {
            "bot_id": bot_id,
            "owner_id": owner_id,
            "code": 1,
            "message": "Health check failed",
            "checked_at": _now_iso(),
            "instances": [],
        }


# ═════════════════════════════════════════════════════════════════════════════
# 4. Bot readiness — 逐 Bot 运行时就绪状态（替代 /health/engine 的前端使用）
# ═════════════════════════════════════════════════════════════════════════════

readiness_router = APIRouter(prefix="/api/system", tags=["system-readiness"])


def _grace_seconds() -> int:
    try:
        return int(os.getenv("BOT_READINESS_GRACE_SECONDS", "60"))
    except ValueError:
        return 60


@readiness_router.get("/readiness", response_model=None)
async def readiness(
    user: AuthenticatedUser = Depends(get_current_user),
    probe: HealthProbePlugin = Injected(HealthProbePlugin),
):
    """逐 Bot 运行时就绪状态。"""
    grace_s = _grace_seconds()
    try:
        bots_raw = await probe.readiness(user.staffId, grace_s)
    except Exception as e:
        logger.error(f"[readiness] derive failed: {e}")
        bots_raw = []

    bots = [BotReadiness(**b) for b in bots_raw]
    return ReadinessResponse(
        checked_at=_now_iso(),
        mode=probe.mode_label,
        total=len(bots),
        bots=bots,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 5. Disk Usage — NAS 一级子目录用量统计
# ═════════════════════════════════════════════════════════════════════════════


class DiskUsageTriggerResponse(BaseModel):
    """触发磁盘用量分析的响应。"""
    message: str
    target_path: str


disk_usage_router = APIRouter(prefix="/api/system", tags=["disk-usage"])


@disk_usage_router.post("/disk-usage", response_model=DiskUsageTriggerResponse)
async def trigger_disk_usage_analysis(
    cooldown_minutes: int = Query(
        10,
        description="冷却时间（分钟）。距离上次分析至少间隔此时间才能再次执行。默认10分钟。设为0则跳过检查。",
        ge=0,
    ),
    skip_within_minutes: int | None = Query(
        None,
        description="跳过最近 N 分钟内更新过的目录（断点续传）。不传则处理所有目录。",
        ge=1,
    ),
    concurrency: int = Query(
        8,
        description="并发数。默认8。",
        ge=1,
        le=64,
    ),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """触发后台分析：并发统计一级子目录大小，实时写入数据库。

    Args:
        cooldown_minutes: 冷却时间（分钟）。距离上次分析至少间隔此时间才能再次执行。
                         默认10分钟。
        skip_within_minutes: 跳过最近 N 分钟内更新过的目录（断点续传）。
                            不传则处理所有目录。
        concurrency: 并发数。默认8，最大64。

    立即返回，后台执行。结果写入 ac_nas_usage_info.total_usage_mb。
    """
    # Admin 权限校验
    if user.staffId not in disk_usage_admin():
        raise HTTPException(status_code=403, detail="权限不足：此接口仅允许管理员调用")

    from agentclaw.community.core.nas_usage import get_nas_usage_service, CooldownError

    service = get_nas_usage_service()

    # 检查冷却时间
    try:
        service.check_cooldown(cooldown_minutes)
    except CooldownError as e:
        return DiskUsageTriggerResponse(
            message=f"Cooldown not elapsed, please wait {e.remaining_minutes:.1f} minutes",
            target_path="/home/admin/.merge_nas",
        )

    triggered = service.trigger_disk_usage_analysis(
        skip_within_minutes=skip_within_minutes,
        cooldown_minutes=cooldown_minutes,
        concurrency=concurrency,
    )
    if not triggered:
        return DiskUsageTriggerResponse(
            message="Disk usage analysis already in progress, please wait",
            target_path="/home/admin/.merge_nas",
        )

    logger.info(f"[disk-usage] Triggered by user={user.staffId} | cooldown={cooldown_minutes}m | skip_within={skip_within_minutes}m | concurrency={concurrency}")
    return DiskUsageTriggerResponse(
        message="Disk usage analysis started, results will be written to ac_nas_usage_info.total_usage_mb",
        target_path="/home/admin/.merge_nas",
    )


@disk_usage_router.post("/disk-usage/file-count", response_model=DiskUsageTriggerResponse)
async def trigger_file_count_analysis(
    cooldown_minutes: int = Query(
        10,
        description="冷却时间（分钟）。距离上次分析至少间隔此时间才能再次执行。默认10分钟。设为0则跳过检查。",
        ge=0,
    ),
    skip_within_minutes: int | None = Query(
        None,
        description="跳过最近 N 分钟内更新过的目录（断点续传）。不传则处理所有目录。",
        ge=1,
    ),
    concurrency: int = Query(
        8,
        description="并发数。默认8。",
        ge=1,
        le=64,
    ),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """触发后台分析：并发统计一级子目录文件数，实时写入数据库。

    Args:
        cooldown_minutes: 冷却时间（分钟）。距离上次分析至少间隔此时间才能再次执行。
                         默认10分钟。
        skip_within_minutes: 跳过最近 N 分钟内更新过的目录（断点续传）。
                            不传则处理所有目录。
        concurrency: 并发数。默认8，最大64。

    立即返回，后台执行。结果写入 ac_nas_usage_info.file_count。
    """
    # Admin 权限校验
    if user.staffId not in disk_usage_admin():
        raise HTTPException(status_code=403, detail="权限不足：此接口仅允许管理员调用")

    from agentclaw.community.core.nas_usage import get_nas_usage_service, CooldownError

    service = get_nas_usage_service()

    # 检查冷却时间
    try:
        service.check_cooldown(cooldown_minutes)
    except CooldownError as e:
        return DiskUsageTriggerResponse(
            message=f"Cooldown not elapsed, please wait {e.remaining_minutes:.1f} minutes",
            target_path="/home/admin/.merge_nas",
        )

    triggered = service.trigger_file_count_analysis(
        skip_within_minutes=skip_within_minutes,
        cooldown_minutes=cooldown_minutes,
        concurrency=concurrency,
    )
    if not triggered:
        return DiskUsageTriggerResponse(
            message="File count analysis already in progress, please wait",
            target_path="/home/admin/.merge_nas",
        )

    logger.info(f"[disk-usage/file-count] Triggered by user={user.staffId} | cooldown={cooldown_minutes}m | skip_within={skip_within_minutes}m | concurrency={concurrency}")
    return DiskUsageTriggerResponse(
        message="File count analysis started, results will be written to ac_nas_usage_info.file_count",
        target_path="/home/admin/.merge_nas",
    )
