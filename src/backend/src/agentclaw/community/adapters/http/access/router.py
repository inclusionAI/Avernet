"""Access control & user management — FastAPI routers.

Replaces:
  - servers/web/routes/user.py      (user_router)
  - servers/web/routes/whitelist.py  (access_router)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.access.schemas import (
    AllowDisallowData,
    AllowRequest,
    ApiResponse,
    QuotaData,
    SetBotsCeilingRequest,
    UpsertUserRequest,
    UserItem,
    WhitelistCheckData,
)
from agentclaw.community.adapters.http.auth.dependencies import get_current_user, require_operator
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.api.user_service import UserServiceProtocol
from agentclaw.community.core.access.errors import UserNotFoundError
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger


logger = get_logger()

# ==================== User Router ====================

user_router = APIRouter(prefix="/api/v1/user", tags=["user"])


@user_router.get("", response_model=ApiResponse[list[UserItem]])
async def list_users(
    user_type: str | None = None,
    svc: UserServiceProtocol = Injected(UserServiceProtocol),
) -> ApiResponse[list[UserItem]]:
    records = svc.list_users(user_type=user_type)
    data = [
        UserItem(
            id=r.id,
            userId=r.user_id,
            userType=r.user_type,
            status=r.status,
            gmtCreate=r.gmt_create,
            gmtModified=r.gmt_modified,
        )
        for r in records
    ]
    return ApiResponse(success=True, message="OK", error_code=200, data=data)


@user_router.get("/{user_type}/{user_id}", response_model=ApiResponse[UserItem | None])
async def get_user(
    user_type: str,
    user_id: str,
    svc: UserServiceProtocol = Injected(UserServiceProtocol),
) -> ApiResponse[UserItem | None]:
    try:
        r = svc.get_user(user_id=user_id, user_type=user_type)
    except UserNotFoundError:
        return ApiResponse(success=False, message="User not found", error_code=404, data=None)
    data = UserItem(
        id=r.id,
        userId=r.user_id,
        userType=r.user_type,
        status=r.status,
        gmtCreate=r.gmt_create,
        gmtModified=r.gmt_modified,
    )
    return ApiResponse(success=True, message="OK", error_code=200, data=data)


@user_router.post("", response_model=ApiResponse[dict])
async def upsert_user(
    req: UpsertUserRequest,
    svc: UserServiceProtocol = Injected(UserServiceProtocol),
) -> ApiResponse[dict]:
    svc.upsert_user(user_id=req.user_id, user_type=req.user_type, status=req.status)
    return ApiResponse(
        success=True,
        message="OK",
        error_code=200,
        data={"userId": req.user_id, "userType": req.user_type, "status": req.status},
    )


# ==================== Access (Whitelist) Router ====================

access_router = APIRouter(prefix="/api/v1/access", tags=["access"])


@access_router.get("/check", response_model=ApiResponse[WhitelistCheckData])
async def check_whitelist(
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
    service: PolicyServiceProtocol = Injected(PolicyServiceProtocol),
) -> ApiResponse[WhitelistCheckData]:
    logger.info(
        "[NEW-ARCH] /api/v1/access/check HIT — staffId=%s, operatorName=%s",
        user.staffId, user.operatorName,
    )
    allowed = service.check(entity_id=user.staffId, entity_type="staff")
    logger.info(
        "[NEW-ARCH] /api/v1/access/check RESULT — staffId=%s, allowed=%s",
        user.staffId, allowed,
    )
    data = WhitelistCheckData(
        label=1 if allowed else 0,
        operator=user.operatorName,
        staffNo=user.staffId,
    )
    return ApiResponse(success=True, message="OK", error_code=200, data=data)


@access_router.post("/allow", response_model=ApiResponse[AllowDisallowData])
async def allow_entity(
    req: AllowRequest,
    user: AuthenticatedUser = Depends(require_operator),  # noqa: B008
    service: PolicyServiceProtocol = Injected(PolicyServiceProtocol),
) -> ApiResponse[AllowDisallowData]:
    service.allow(entity_id=req.entity_id, entity_type=req.entity_type)
    data = AllowDisallowData(entity_id=req.entity_id, entity_type=req.entity_type)
    return ApiResponse(success=True, message="OK", error_code=200, data=data)


@access_router.post("/disallow", response_model=ApiResponse[AllowDisallowData])
async def disallow_entity(
    req: AllowRequest,
    user: AuthenticatedUser = Depends(require_operator),  # noqa: B008
    service: PolicyServiceProtocol = Injected(PolicyServiceProtocol),
) -> ApiResponse[AllowDisallowData]:
    service.disallow(entity_id=req.entity_id, entity_type=req.entity_type)
    data = AllowDisallowData(entity_id=req.entity_id, entity_type=req.entity_type)
    return ApiResponse(success=True, message="OK", error_code=200, data=data)


@access_router.get("/quota", response_model=ApiResponse[QuotaData])
async def get_quota(
    service: PolicyServiceProtocol = Injected(PolicyServiceProtocol),
) -> ApiResponse[QuotaData]:
    try:
        raw = service.get_quota()
        data = QuotaData(
            quota=raw["quota"],
            totalLimit=raw["totalLimit"],
            activeCount=raw["activeCount"],
            effectiveQuota=raw["effectiveQuota"],
            updateTime=raw["updateTime"],
        )
        return ApiResponse(success=True, message="OK", error_code=200, data=data)
    except Exception as e:
        logger.error("[access_router.get_quota] error: %s", e, exc_info=True)
        return ApiResponse(
            success=False,
            message="获取配额失败",
            error_code=500,
            data=None,
        )


@access_router.put("/bots-ceiling", response_model=ApiResponse[dict])
async def set_bots_ceiling(
    req: SetBotsCeilingRequest,
    user: AuthenticatedUser = Depends(require_operator),  # noqa: B008
    service: PolicyServiceProtocol = Injected(PolicyServiceProtocol),
) -> ApiResponse[dict]:
    """PUT /api/v1/access/bots-ceiling — 设置指定用户的 BOT 数量上限。

    仅 operator 白名单用户可调用。在 ``ac_access_control_policy.policy``
    JSON 中 merge 写入 ``bots_ceiling``，保留其他 key。
    """
    try:
        service.set_bots_ceiling(entity_id=req.entity_id, ceiling=req.ceiling)
        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data={"entityId": req.entity_id, "ceiling": req.ceiling},
        )
    except Exception as e:
        logger.error(f"[access_router.set_bots_ceiling] error: {e}", exc_info=True)
        return ApiResponse(
            success=False,
            message=f"设置 BOT 上限失败: {str(e)}",
            error_code=500,
            data=None,
        )
