"""Legacy skills publication routes."""

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    DownloadUrlResponse,
    FileContentResponse,
    FileStructureResponse,
    MarketSearchResponse,
    MarketTagsResponse,
    MessageResponse,
    PublishSkillRequest,
    PublishStatusResponse,
    VersionListResponse,
)
from agentclaw.community.api.skill_publish_service import SkillPublishServiceProtocol
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterClient,
    SkillCenterMarketSearchRequest,
)
from fastapi import (
    Depends,
    HTTPException,
    Query,
)

from .skills_common import (
    logger,
)


from fastapi import APIRouter
router = APIRouter(prefix="/api/skills", tags=["skills"])

@router.post("/{skill_id}/publish", response_model=PublishStatusResponse)
async def publish_skill(
    skill_id: str,
    request: PublishSkillRequest | None = None,
    ctx: RequestContext = Depends(get_request_context),
    service: SkillPublishServiceProtocol = Injected(SkillPublishServiceProtocol),
):
    """触发技能发布 — developing/rejected → pending"""
    from agentclaw.community.core.skill_center.services.skill_publish_service import (
        InvalidTransitionError,
    )

    try:
        user_id = request.user_id if request else ctx.user_id
        zip_path = request.zip_path if request else None
        package_url = request.package_url if request else None
        result = service.publish(
            skill_id,
            user_id=user_id,
            zip_path=zip_path,
            package_url=package_url,
            nick_name=ctx.nick_name,
        )
        return PublishStatusResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{skill_id}/publish/upgrade", response_model=PublishStatusResponse)
async def publish_upgrade_skill(
    skill_id: str,
    request: PublishSkillRequest | None = None,
    ctx: RequestContext = Depends(get_request_context),
    service: SkillPublishServiceProtocol = Injected(SkillPublishServiceProtocol),
):
    """升级发布 — published → 新建行 pending"""

    try:
        user_id = request.user_id if request else ctx.user_id
        result = service.publish_upgrade(skill_id, user_id=user_id)
        return PublishStatusResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{skill_id}/publish/status", response_model=PublishStatusResponse)
async def get_publish_status(
    skill_id: str,
    ctx: RequestContext = Depends(get_request_context),
    service: SkillPublishServiceProtocol = Injected(SkillPublishServiceProtocol),
):
    """轮询发布状态 — 仅 pending 可调用"""

    try:
        result = service.query_status(skill_id)
        return PublishStatusResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{skill_id}/versions", response_model=VersionListResponse)
async def list_skill_versions(
    skill_id: str,
    ctx: RequestContext = Depends(get_request_context),
    service: SkillPublishServiceProtocol = Injected(SkillPublishServiceProtocol),
):
    """获取技能版本列表"""

    try:
        versions = service.list_versions(skill_id)
        return VersionListResponse(success=True, data=versions, count=len(versions))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/market/center/search", response_model=MarketSearchResponse)
async def search_market_center(
    keyword: str = Query("", description="搜索关键词"),
    tag: str = Query("", description="标签过滤"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    client: SkillCenterClient = Injected(SkillCenterClient),
):
    """搜索 SkillCenter 公开市场技能（透传）"""

    try:
        result = client.search_market_skills(
            SkillCenterMarketSearchRequest(
                keyword=keyword,
                tag_list=(tag,) if tag else (),
                page_num=page,
                page_size=page_size,
                access_level="PUBLIC",
            )
        )
        return MarketSearchResponse(
            success=True,
            data=list(result.items),
            total=result.total,
        )
    except Exception as e:
        logger.error("[search_market_center] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market/center/tags", response_model=MarketTagsResponse)
async def get_market_tags_center(
    client: SkillCenterClient = Injected(SkillCenterClient),
):
    """获取 SkillCenter 市场标签列表（透传）"""

    try:
        tags = client.get_market_tags()
        return MarketTagsResponse(success=True, data=tags)
    except Exception as e:
        logger.error("[get_market_tags_center] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/market/center/{skill_code}/install", response_model=MessageResponse)
async def install_market_skill(skill_code: str):
    """从 SkillCenter 公开市场安装技能（占位，后续扩展下载+创建本地记录）"""
    return MessageResponse(
        success=True, message=f"技能 {skill_code} 安装接口已就绪，完整安装流程后续迭代"
    )


@router.get(
    "/{skill_id}/versions/{version_number}/download-url",
    response_model=DownloadUrlResponse,
)
async def get_version_download_url(
    skill_id: str,
    version_number: str,
    ctx: RequestContext = Depends(get_request_context),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    client: SkillCenterClient = Injected(SkillCenterClient),
):
    """获取指定版本的下载 URL（透传 SkillCenter）"""

    service = skill_service_factory.create()
    skill = service.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    skill_code = skill.get("skill_uuid") or skill.get("link_name") or skill_id
    try:
        result = client.get_download_url(skill_code, version_number)
        return DownloadUrlResponse(
            success=result.get("success", False),
            data=result.get("data", {}),
            message=result.get("message", ""),
        )
    except Exception as e:
        logger.error("[get_version_download_url] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{skill_id}/file-structure", response_model=FileStructureResponse)
async def get_skill_file_structure(
    skill_id: str,
    version: str = Query("", description="版本号（可选，默认最新版本）"),
    ctx: RequestContext = Depends(get_request_context),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    client: SkillCenterClient = Injected(SkillCenterClient),
):
    """获取技能文件结构树（透传 SkillCenter）"""

    service = skill_service_factory.create()
    skill = service.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    skill_code = skill.get("skill_uuid") or skill.get("link_name") or skill_id
    try:
        result = client.get_file_structure(skill_code, version)
        return FileStructureResponse(
            success=result.get("success", False),
            data=result.get("data", {}),
            message=result.get("message", ""),
        )
    except Exception as e:
        logger.error("[get_skill_file_structure] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{skill_id}/file-content", response_model=FileContentResponse)
async def get_skill_file_content(
    skill_id: str,
    file_path: str = Query(..., alias="filePath", description="文件相对路径"),
    version: str = Query("", description="版本号（可选，默认最新版本）"),
    ctx: RequestContext = Depends(get_request_context),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    client: SkillCenterClient = Injected(SkillCenterClient),
):
    """获取技能指定文件内容（透传 SkillCenter）"""

    service = skill_service_factory.create()
    skill = service.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    skill_code = skill.get("skill_uuid") or skill.get("link_name") or skill_id
    try:
        result = client.get_file_content(skill_code, file_path, version)
        return FileContentResponse(
            success=result.get("success", False),
            data=result.get("data", {}),
            message=result.get("message", ""),
        )
    except Exception as e:
        logger.error("[get_skill_file_content] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
