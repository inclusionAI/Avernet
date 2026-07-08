from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter

from agentclaw.community.adapters.http.yuque.schemas import (
    YuqueVerifyData,
    YuqueVerifyRequest,
    YuqueVerifyResponse,
)
from agentclaw.community.di import Injected
from agentclaw.community.di.config import YuqueConfig
from agentclaw.community.plugin_api.http_client import QUALIFIER_GENERAL, HttpClient

router = APIRouter()


def _first_segment(url: str) -> str:
    path = urlparse(url).path.lstrip("/")
    if not path:
        return ""
    return path.split("/", 1)[0]


@router.post("/api/v1/yuque/verify", response_model=YuqueVerifyResponse)
def verify_yuque_binding(
    body: YuqueVerifyRequest,
    http: Annotated[HttpClient, QUALIFIER_GENERAL] = Injected(Annotated[HttpClient, QUALIFIER_GENERAL]),
    yuque_config: YuqueConfig = Injected(YuqueConfig),
) -> YuqueVerifyResponse:
    namespace = _first_segment(body.url)
    if not namespace:
        return YuqueVerifyResponse(success=False, error="URL 缺少第一层路径")

    if not yuque_config.user_api:
        return YuqueVerifyResponse(success=False, error="语雀校验未配置 (yuque.user_api 未设置)")

    try:
        resp = http.get(yuque_config.user_api, headers={"X-Auth-Token": body.team_token})
    except httpx.HTTPError as exc:
        return YuqueVerifyResponse(success=False, error=f"调用语雀失败: {exc}")

    if resp.status_code != 200:
        return YuqueVerifyResponse(
            success=False,
            error=f"语雀返回 {resp.status_code}: {resp.text[:200]}",
        )

    payload = resp.json() or {}
    user = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    login = (user or {}).get("login", "")

    return YuqueVerifyResponse(
        success=True,
        data=YuqueVerifyData(bound=login == namespace, login=login, namespace=namespace),
    )
