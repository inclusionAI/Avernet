"""Legacy SkillCenter diagnostic routes.

The retired per-Skill NAS sync address answers 410 and points callers to the
canonical environment-wide G4 public operation.  This module no longer owns
``current`` symlinks, name mapping, or caller-selected versions.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentclaw.community.di import Injected
from agentclaw.community.api.skill_center_sync_service import SkillCenterSyncServiceProtocol
from agentclaw.community.api.skill_propagation_service import SkillPropagationServiceProtocol
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.log import get_logger


logger = get_logger()
router = APIRouter(prefix="/api/v1/skill-center", tags=["skill-center"])


@router.post("/sync", deprecated=True)
async def retired_force_sync_skill() -> None:
    """Retired: use POST /openapi/v1/bots/market/skill-center/sync."""
    raise HTTPException(
        status_code=410,
        detail="Use POST /openapi/v1/bots/market/skill-center/sync",
    )


class BootstrapResponse(BaseModel):
    synced: int
    failed: int
    skipped: int
    error: str | None = None


class RefreshBotRequest(BaseModel):
    bot_id: str
    owner_id: str
    skill_uuid: str
    engine_type: str = "openclaw"
    env: str | None = None


class RefreshBotResponse(BaseModel):
    success: bool
    bot_id: str
    skill_uuid: str
    message: str


@router.post("/refresh-bot", response_model=RefreshBotResponse)
async def refresh_bot_symlinks(
    request: RefreshBotRequest,
    svc: SkillPropagationServiceProtocol = Injected(SkillPropagationServiceProtocol),
):
    """手动触发单个 Bot 的软链刷新 — T4 测试用。

    调用 SkillPropagationService._refresh_bot(bot_id, owner_id, skill_uuid, engine_type, env)。
    内部会调 SkillSetService.get_symlink_mappings() + ArcaDeviceSyncPlugin.sync_symlinks()。
    """
    logger.info("[SkillPropagation] API /refresh-bot called: bot_id=%s skill_uuid=%s",
                request.bot_id, request.skill_uuid)

    try:
        ok = svc._refresh_bot(
            bot_id=request.bot_id,
            owner_id=request.owner_id,
            skill_uuid=request.skill_uuid,
            engine_type=request.engine_type,
            env=request.env,
        )
        return RefreshBotResponse(
            success=ok,
            bot_id=request.bot_id,
            skill_uuid=request.skill_uuid,
            message="refresh ok" if ok else "refresh failed (see logs)",
        )
    except Exception as exc:
        logger.exception("[SkillPropagation] API /refresh-bot failed: bot_id=%s error=%s",
                        request.bot_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/bootstrap", response_model=BootstrapResponse)
async def trigger_bootstrap(
    svc: SkillCenterSyncServiceProtocol = Injected(SkillCenterSyncServiceProtocol),
):
    """手动触发 bootstrap — 启动时批量同步所有 center:// 已发布 skill。

    等价于服务启动时的 startup_skill_center_sync_service() 逻辑。
    用于测试：避免每次重启服务来触发 bootstrap。
    """
    logger.info("[SkillCenterSyncService] API /bootstrap called")
    try:
        result = await svc.sync_bootstrap()
        return BootstrapResponse(
            synced=result.updated,
            failed=result.failed,
            skipped=result.unchanged,
        )
    except Exception as exc:
        logger.exception("[SkillCenterSyncService] API /bootstrap failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sync-categories")
@router.post("/sync-categories")
async def sync_categories(
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
):
    """扫描 git 仓库 skills 目录，把类目层级写入 ac_skill_category 表。

    仅扫描不含 SKILL.md 的中间目录作为类目节点。
    幂等：已存在的 path 会跳过。

    浏览器直接访问：GET /api/v1/skill-center/sync-categories
    """
    logger.info("[SkillService] API /sync-categories called")
    try:
        svc = skill_service_factory.create()
        result = svc.sync_categories_from_git()
        return {"success": True, **result}
    except Exception as exc:
        logger.exception("[SkillService] API /sync-categories failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
