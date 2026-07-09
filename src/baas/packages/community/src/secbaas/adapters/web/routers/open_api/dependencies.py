"""Open API Run 依赖注入"""

from dependency_injector.wiring import Provide, inject
from fastapi import Depends, Header, HTTPException, Request, status

from secbaas.api.api_gateway import (
    APIKeyPolicy,
    APIKeyRecord,
    APIKeyValidator,
    parse_policy,
)
from secbaas.api.bot_runtime import BotChatContext
from secbaas.api.open_api import OpenAPICode
from secbaas.bootstrap import ApplicationContainer


def get_api_key_from_header(authorization: str | None = Header(None)) -> str:
    """从 Authorization Header 解析 Bearer Token

    Args:
        authorization: Authorization Header 值，格式为 "Bearer {api_key}"

    Returns:
        api_key: 解析出的 API Key

    Raises:
        HTTPException: 401 如果 Header 缺失或格式错误
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 40101, "message": "Token 缺失"},
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": 40002, "message": "参数缺失"},
        )

    return parts[1]


def get_iam_token_from_cookie(request: Request) -> str | None:
    """从 Cookie 解析 IAM Token（忽略大小写）

    Args:
        request: FastAPI Request 对象

    Returns:
        iam_token: 解析出的 IAM Token，不存在时返回 None
    """
    # 忽略大小写查找 cookie
    for key, value in request.cookies.items():
        if key.upper() == "IAM_TOKEN":
            return value if value else None

    return None


@inject
async def validate_api_key(
    api_key: str = Depends(get_api_key_from_header),
    validator: APIKeyValidator = Depends(
        Provide[ApplicationContainer.services.api_key_validator]
    ),
) -> APIKeyRecord:
    """验证 API Key 并返回关联的 API Key 记录

    Args:
        api_key: API Key
        validator: APIKeyValidator 实例

    Returns:
        APIKeyRecord: 验证通过的 API Key 记录，包含 bot_id (app_id)、api_key_prefix 等

    Raises:
        HTTPException: 401 如果 API Key 无效
    """
    record = await validator.verify(api_key)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 40103, "message": "Token 无效"},
        )
    return record


def get_bot_chat_context(
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    iam_token: str | None = Depends(get_iam_token_from_cookie),
) -> BotChatContext:
    """从 API Key 记录和 Cookie 构建 BotChatContext

    Args:
        api_key_record: API Key 验证记录
        iam_token: 从 Cookie 中提取的 IAM Token

    Returns:
        BotChatContext: 请求上下文
    """
    return BotChatContext.from_api_key(
        api_key_prefix=api_key_record.api_key_prefix,
        app_id=api_key_record.app_id,
        app_type=api_key_record.app_type or "UNKNOWN",
        iam_token=iam_token,
        tenant=api_key_record.tenant or "",
    )


def _normalize_bot_id(bot_id: str) -> str:
    """兼容处理 bot id 中工号带 0 的情况。bot_id 格式 ``<real_bot_id>:<entity_id>``，去除 entity_id 前导零。"""
    if ":" in bot_id:
        real_bot_id, entity_id = bot_id.rsplit(":", 1)
        entity_id = entity_id.lstrip("0") or "0"
        return f"{real_bot_id}:{entity_id}"
    return bot_id


def resolve_bot_id_from_api_key(api_key_record: APIKeyRecord) -> str:
    """从 API Key 记录解析 bot_id，兼容工号前导零

    Args:
        api_key_record: API Key 验证记录

    Returns:
        str: 解析并归一化后的 bot_id
    """
    return _normalize_bot_id(api_key_record.app_id)


def parse_bot_id(bot_id: str) -> tuple[str, str]:
    """解析 bot_id 为 real_bot_id 和 entity_id

    Args:
        bot_id: bot ID，格式为 <real_bot_id>:<entity_id>

    Returns:
        tuple[str, str]: (real_bot_id, entity_id)
    """
    parts = bot_id.split(":", 1)
    real_bot_id = parts[0] if parts else ""
    entity_id = parts[1] if len(parts) == 2 else ""
    return real_bot_id, entity_id


def match_allowed_bots(target_bot_id: str, allowed_bots: list[str]) -> str | None:
    """校验 target_bot_id 是否在 allowed_bots 列表中

    Args:
        target_bot_id: 目标 bot ID，格式为 <real_bot_id>:<entity_id>
        allowed_bots: 允许访问的 bot ID 列表

    Returns:
        str | None: 匹配到的 allowed_bots 中的 bot_id（权威格式），未匹配返回 None
    """
    if not allowed_bots:
        return None

    target_real_bot_id, target_entity_id = parse_bot_id(target_bot_id)

    for allowed in allowed_bots:
        allowed_real_bot_id, allowed_entity_id = parse_bot_id(allowed)

        if target_real_bot_id != "default":
            # 非 default 模式：只需要 real_bot_id 匹配即可
            if target_real_bot_id == allowed_real_bot_id:
                return allowed
        else:
            # default 模式：需要完整匹配（real_bot_id:entity_id）
            if (
                target_real_bot_id == allowed_real_bot_id
                and target_entity_id == allowed_entity_id
            ):
                return allowed

    return None


def validate_policy(
    api_key_record: APIKeyRecord,
    target_bot_id: str,
) -> str:
    """根据 API Key 的 policy 校验对目标 bot 的访问权限

    policy 语义（parse_policy 归一化后）：
    - allowed_bots 含 "*"：允许访问所有 bot（含历史未配置的存量 key）。
    - allowed_bots 为空（含历史 ["NONE"] 哨兵）：拒绝所有 bot（fail-closed）。
    - 否则：白名单匹配，仅命中的 bot 放行。

    Args:
        api_key_record: API Key 记录
        target_bot_id: 目标 bot ID，格式为 <real_bot_id>:<entity_id>

    Returns:
        str: 校验通过后应使用的 bot_id。
             当为显式 allow-all 时返回原始 target_bot_id；
             当为白名单匹配时返回匹配到的 allowed_bots 中的权威格式。

    Raises:
        HTTPException: 403 如果无权限
    """
    policy = parse_policy(api_key_record.policy)

    if APIKeyPolicy.ALL in policy.allowed_bots:
        # 显式 allow-all（含历史未配置存量 key 的归一结果）
        return target_bot_id

    matched = match_allowed_bots(target_bot_id, policy.allowed_bots)
    if matched is None:
        # 白名单未命中 / 空（含 NONE）即拒绝所有 bot（fail-closed）
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": OpenAPICode.FORBIDDEN,
                "message": f"API Key 无权访问 bot: {target_bot_id}",
            },
        )
    return matched
