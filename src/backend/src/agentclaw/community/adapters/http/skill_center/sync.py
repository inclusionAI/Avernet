"""SkillCenter Sync 测试接口 — 仅用于 DEV 环境手动测试 T2。

POST /api/v1/skill-center/sync
Body: { "skill_id": "...", "env": "dev", "version": "..." (可选) }

说明：
  - skill_id: ac_skill.id（本地主键），通过 skill_id 查表拿到 skill_uuid（跨版本标识）再调 SC API
  - 内部会根据 ac_skill.name 查询 SC 的 skillCode

响应: { "success": true, "skill_id": "...", "skill_uuid": "...", "skill_code": "...", "version": "...", "message": "..." }
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentclaw.community.core.skill_center.services.repositories import SkillRepository
from agentclaw.community.di import Injected
from agentclaw.community.api.skill_center_sync_service import SkillCenterSyncServiceProtocol
from agentclaw.community.api.skill_propagation_service import SkillPropagationServiceProtocol
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.log import get_logger


logger = get_logger()
router = APIRouter(prefix="/api/v1/skill-center", tags=["skill-center"])


class SyncRequest(BaseModel):
    skill_id: str
    env: str = "dev"
    version: str | None = None


class SyncResponse(BaseModel):
    success: bool
    skill_id: str
    skill_uuid: str
    skill_code: str
    version: str | None
    message: str


@router.post("/sync", response_model=SyncResponse)
async def force_sync_skill(
    request: SyncRequest,
    skill_repo: SkillRepository = Injected(SkillRepository),
    sync_svc: SkillCenterSyncServiceProtocol = Injected(SkillCenterSyncServiceProtocol),
):
    """手动触发 force_sync — T2 测试用。

    仅限 DEV 环境使用。

    流程：skill_id → 查 ac_skill 表 → 拿到 skill_uuid（NAS 路径用）和 name（SC skillCode）→ 调 force_sync
    """
    logger.info("[SkillCenterSyncService] API /sync called: skill_id=%s env=%s version=%s",
                request.skill_id, request.env, request.version)

    # 用 skill_id 查 ac_skill 表
    skill_record = skill_repo.get_by_id(request.skill_id, env=request.env)
    if not skill_record:
        raise HTTPException(status_code=404, detail=f"skill not found: skill_id={request.skill_id}")

    skill_uuid = skill_record.get("skill_uuid") or request.skill_id
    skill_code = skill_record.get("name", "")

    if not skill_code:
        raise HTTPException(status_code=400, detail=f"skill has no name (SC skillCode): skill_id={request.skill_id}")

    logger.info("[SkillCenterSyncService] API resolved: skill_id=%s -> skill_uuid=%s skill_code=%s",
                request.skill_id, skill_uuid, skill_code)

    try:
        result = sync_svc.force_sync(
            skill_uuid=skill_uuid,
            env=request.env,
            version=request.version,
        )
        return SyncResponse(
            success=True,
            skill_id=request.skill_id,
            skill_uuid=skill_uuid,
            skill_code=skill_code,
            version=request.version,
            message="sync completed" if result else "skipped (already synced)",
        )
    except Exception as exc:
        logger.exception("[SkillCenterSyncService] API /sync failed: skill_id=%s error=%s",
                         request.skill_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
        return BootstrapResponse(**result)
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
