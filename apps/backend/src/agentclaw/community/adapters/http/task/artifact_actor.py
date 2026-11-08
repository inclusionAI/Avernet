"""产物写口的 actor 依赖(spec §14.1:双凭证分支,HTTP 层只转协议)。

两个产物写路由(``POST /artifacts/object-file``、``POST /artifacts``)共用本
依赖做凭证分支选择——本模块**不看图、不派生 creator**,只按 header 有无
决定走 relay 尝试还是用户登录态;领域语义(token fence、creator=assignee)
全部在核心服务收口(Rule 22)。空串/纯空白 ``X-Relay-Token`` 视为未带
(§14.5:falsy → 用户分支)。
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.auth import AuthPlugin

_RELAY_TOKEN_HEADER = "x-relay-token"


@dataclass(frozen=True)
class ArtifactWriteActor:
    """二选一的写侧身份:relay_token(尝试凭证)或 user(登录态)。"""

    relay_token: str | None  # relay 分支非 None;用户分支恒 None
    user: AuthenticatedUser | None  # 用户分支非 None;relay 分支恒 None


async def resolve_artifact_write_actor(
    request: Request,
    auth_plugin: AuthPlugin = Injected(AuthPlugin),  # noqa: B008
) -> ArtifactWriteActor:
    """Header 带 X-Relay-Token → relay 尝试分支(不触碰 AuthPlugin,不发 SSO 调用)。

    未带 → 用户登录态分支,委托既有 ``get_current_user``(401/302 既有语义
    原样穿透)。
    """
    token = (request.headers.get(_RELAY_TOKEN_HEADER) or "").strip() or None
    if token is not None:
        return ArtifactWriteActor(relay_token=token, user=None)
    # 类型已由 get_current_user 保证非 None(未认证直接抛 Unauthorized/302)。
    user = await get_current_user(request, auth_plugin)
    return ArtifactWriteActor(relay_token=None, user=user)


__all__ = ["ArtifactWriteActor", "resolve_artifact_write_actor"]