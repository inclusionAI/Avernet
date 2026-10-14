"""Route-local response/error translation; leaves old OpenAPI contracts intact."""

from typing import Generic, TypeVar
from uuid import uuid4

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.responses import JSONResponse

from agentclaw.community.core.spaces.management_models import ManagementError
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterTeamCreateError,
)

T = TypeVar("T")
logger = get_logger()


class ManagementResponse(BaseModel, Generic[T]):
    code: str = "OK"
    message: str = "操作成功"
    data: T
    request_id: str


ERRORS = {
    "SPACE_AUTH_REQUIRED": (401, "无法获取有效的登录工号，请使用有效登录凭据重试。"),
    "SPACE_ADMIN_REQUIRED": (403, "当前账号没有空间管理接口权限，请联系管理员确认超级管理员配置。"),
    "SPACE_TYPE_INVALID": (
        422,
        "仅支持创建团队空间，space_type 必须为 TEAM；不传时默认 TEAM。",
    ),
    "SPACE_NOT_FOUND": (404, "空间不存在或已被删除，请确认空间 ID。"),
    "SPACE_TYPE_NOT_SUPPORTED": (409, "该接口仅支持团队空间，不能操作个人空间。"),
    "SPACE_HAS_MEMBERS": (
        409,
        "该空间还有成员，暂时无法单独删除。请先移除全部成员，或使用“删除空间及成员”接口。",
    ),
    "SPACE_NAME_ALREADY_EXISTS": (
        409,
        "该创建者在当前环境下已存在同名团队空间，请更换名称。",
    ),
    "SPACE_MEMBERS_NOT_FOUND": (
        409,
        "部分员工尚未加入该空间，请先添加成员后再设置角色。本次未修改任何成员角色。",
    ),
    "REQUEST_INVALID": (
        422,
        "请求参数有误，请检查字段、员工工号、角色及分页范围；批量操作需提供 1～500 项工号。",
    ),
    "SKILL_CENTER_CREATE_FAILED": (
        502,
        "空间创建未完成，Skill Center 暂时无法完成处理。请联系管理员确认结果后再重试。",
    ),
    "SPACE_OPERATION_FAILED": (500, "操作未完成，请保留请求编号并联系管理员排查。"),
}


def success(request: Request, data: T) -> ManagementResponse[T]:
    return ManagementResponse(
        data=data, request_id=request.state.space_management_request_id
    )


def failure(request: Request, code: str, data: dict) -> JSONResponse:
    status, message = ERRORS[code]
    return JSONResponse(
        status_code=status,
        content=jsonable_encoder(
            ManagementResponse(
                code=code,
                message=message,
                data=data,
                request_id=request.state.space_management_request_id,
            )
        ),
    )


class ManagementRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            request.state.space_management_request_id = str(uuid4())
            try:
                response = await handler(request)
            except RequestValidationError as exc:
                invalid_type = any(
                    error["loc"] == ("body", "space_type") for error in exc.errors()
                )
                code = "SPACE_TYPE_INVALID" if invalid_type else "REQUEST_INVALID"
                response = failure(request, code, {})
            except ManagementError as exc:
                response = failure(request, exc.code, exc.details)
            except SkillCenterTeamCreateError:
                response = failure(request, "SKILL_CENTER_CREATE_FAILED", {})
            # Authentication/authorization errors remain owned by the existing
            # app handlers. Unexpected write failures are sanitized in endpoints.
            response.headers["X-Request-ID"] = request.state.space_management_request_id
            return response

        return handle


def execute(request: Request, operation, *args):
    """Sanitize unexpected service failures without swallowing failed writes."""
    try:
        return success(request, operation(*args))
    except (ManagementError, SkillCenterTeamCreateError):
        raise
    except Exception as exc:
        logger.error(
            "space maintenance failed request_id=%s operation=%s error_type=%s",
            request.state.space_management_request_id,
            operation.__name__,
            type(exc).__name__,
        )
        return failure(request, "SPACE_OPERATION_FAILED", {})
