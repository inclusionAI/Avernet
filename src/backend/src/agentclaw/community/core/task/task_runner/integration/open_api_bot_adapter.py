"""OpenApiBotAdapter:BaaS Open API 单 bot 派发(httpx async,对齐 send_bot_message.py)。

ensure_grant:GET allowed-bots → 缺则 POST grant(Cookie+Referer 登录态,非 Bearer)。
send_message:POST /openapi/v1/messages(Bearer)→ message_id(=run_id);顺带回包 session_id(BaaS 当前未返时前向兼容为 None,将来补字段自动落库)。
get_run:GET /openapi/v1/messages/{id}→ {status,result,error}(status 大小写不敏感)。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from agentclaw.community.core.task.task_runner.integration.ports import ApiKeyProvider, BotSendResult, OpenApiBotPort


# api_key_prefix 未设置时,回落取 api_key 前多少位作 URL 路径段。
# 取 10:与 IntegrationDouble/_Key 约定一致(api_key="ak1234567890" → prefix="ak12345678")。
# 不同环境可在 ApiKeyProvider.api_key_prefix 显式提供真实前缀(优先),此处仅兜底。
_DEFAULT_KEY_PREFIX_LEN = 8

logger = logging.getLogger(__name__)


def _resp_summary(resp) -> str:
    """Bounded response body for logging (no auth headers)."""
    try:
        return (resp.text or "").replace("\n", " ")[:500]
    except Exception:
        return "<unreadable>"


class OpenApiError(Exception):
    ...


class OpenApiAuthError(OpenApiError):
    ...  # 401/403 grant 失败不可重试


class OpenApiBadRequestError(OpenApiError):
    ...  # 4xx 不重试


class OpenApiRateLimitError(OpenApiError):
    ...  # 429 可重试


class OpenApiServerError(OpenApiError):
    ...  # 5xx 可重试


class OpenApiTimeoutError(OpenApiError):
    ...


def parse_bot_id(bot_id: str) -> tuple[str, str]:
    real, _, entity = bot_id.partition(":")
    return real, entity


def _map_status(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise OpenApiAuthError(f"{resp.status_code} {resp.text}")
    if resp.status_code == 429:
        raise OpenApiRateLimitError(f"429 {resp.text}")
    if 400 <= resp.status_code < 500:
        raise OpenApiBadRequestError(f"{resp.status_code} {resp.text}")
    if resp.status_code >= 500:
        raise OpenApiServerError(f"{resp.status_code} {resp.text}")


class OpenApiBotAdapter(OpenApiBotPort):  # pragma: no cover — live BaaS OpenApi HTTP client; exercised by singlebox/corp acceptance / 联调, not CI LOCAL line coverage
    def __init__(self, keys: ApiKeyProvider, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._k = keys
        self._client = http_client or httpx.AsyncClient(base_url=keys.base_url)

    async def _aclose(self) -> None:
        await self._client.aclose()

    async def ensure_grant(self, bot_id: str) -> None:
        prefix = self._k.api_key_prefix or self._k.api_key[:_DEFAULT_KEY_PREFIX_LEN]
        logger.info("[task][openapi_bot] >>> ensure_grant GET /api/v1/api-keys/%s/allowed-bots bot_id=%s base_url=%s",
                    prefix, bot_id, self._k.base_url)
        r = await self._client.get(f"/api/v1/api-keys/{prefix}/allowed-bots",
                                   headers={"Authorization": f"Bearer {self._k.api_key}"})
        logger.info("[task][openapi_bot] <<< ensure_grant GET allowed-bots status=%s body=%s",
                    r.status_code, _resp_summary(r))
        _map_status(r)
        allowed = (r.json().get("data") or {}).get("allowed_bots") or []
        if bot_id in allowed:
            logger.info("[task][openapi_bot] ensure_grant bot_id=%s 已在 allowed_bots,跳过 grant", bot_id)
            return
        logger.info("[task][openapi_bot] >>> ensure_grant POST grant bot_id=%s (cookie_set=%s referer_set=%s)",
                    bot_id, bool(self._k.cookie), bool(self._k.referer))
        g = await self._client.post(f"/api/v1/api-keys/{prefix}/allowed-bots/grant",
                                    json={"bot_id": bot_id},
                                    headers={"Cookie": self._k.cookie, "Referer": self._k.referer})
        logger.info("[task][openapi_bot] <<< ensure_grant POST grant status=%s body=%s",
                    g.status_code, _resp_summary(g))
        if g.status_code in (401, 403):
            raise OpenApiAuthError(f"grant {g.status_code} {g.text}")
        _map_status(g)

    async def send_message(self, *, bot_id: str, message: str, metadata: dict[str, Any]) -> BotSendResult:
        logger.info("[task][openapi_bot] >>> send_message POST /openapi/v1/messages bot_id=%s base_url=%s msg_len=%s",
                    bot_id, self._k.base_url, len(message or ""))
        r = await self._client.post("/openapi/v1/messages",
                                    json={"bot_id": bot_id, "message": message},
                                    headers={"Authorization": f"Bearer {self._k.api_key}"})
        logger.info("[task][openapi_bot] <<< send_message status=%s body=%s",
                    r.status_code, _resp_summary(r))
        _map_status(r)
        data = r.json().get("data") or {}
        # message_id 即 run_id;session_id 前向兼容——当前 BaaS 回包未提供时为 None,将来 BaaS 补字段自动落库。
        logger.info("[task][openapi_bot] send_message 结果 message_id(run_id)=%s session_id=%s",
                    data.get("message_id"), data.get("session_id"))
        return BotSendResult(run_id=data.get("message_id"), session_id=data.get("session_id"))

    async def get_run(self, run_id: str) -> dict[str, Any]:
        logger.info("[task][openapi_bot] >>> get_run GET /openapi/v1/messages/%s base_url=%s", run_id, self._k.base_url)
        r = await self._client.get(f"/openapi/v1/messages/{run_id}",
                                   headers={"Authorization": f"Bearer {self._k.api_key}"})
        logger.info("[task][openapi_bot] <<< get_run status=%s body=%s", r.status_code, _resp_summary(r))
        _map_status(r)
        return r.json().get("data") or {}

    async def cancel_run(self, run_id: str) -> None:
        """Best-effort stop tracking hook.

        当前 BaaS Open API 未暴露任务模块可用的 cancel endpoint；Poller 注销 handle 后不再消费晚到结果。
        Singlebox 实现会真实取消本地 WebSocket collector。
        """
        return None

    def send_and_wait(self, *, bot_id: str, message: str, metadata: dict[str, Any] | None = None,
                      timeout: float = 180.0, poll_interval: float = 2.0) -> dict[str, Any]:
        """同步便捷接口:ensure_grant → send_message → 轮询 get_run 到终态(COMPLETED/FAILED)。

        在自有新事件循环上跑完整个 async 链路(对齐 run_execute 模式),供同步调用方一次拿回答。
        终态返回 get_run 的 data dict;超时(默认 180s=3 分钟)抛 OpenApiTimeoutError;
        grant 403 → OpenApiAuthError、5xx → OpenApiServerError 透传给同步调用方。
        """
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self.send_and_wait_async(bot_id=bot_id, message=message, metadata=metadata,
                                     timeout=timeout, poll_interval=poll_interval))
        finally:
            loop.close()

    async def send_and_wait_async(self, *, bot_id: str, message: str,
                                   metadata: dict[str, Any] | None, timeout: float,
                                   poll_interval: float) -> dict[str, Any]:
        await self.ensure_grant(bot_id)
        sent = await self.send_message(bot_id=bot_id, message=message, metadata=metadata or {})
        run_id = sent.run_id
        deadline = time.monotonic() + timeout
        last_status = ""
        while True:
            run = await self.get_run(run_id)
            last_status = str(run.get("status") or "")
            if last_status.upper() in ("COMPLETED", "FAILED"):
                return run
            if time.monotonic() >= deadline:
                raise OpenApiTimeoutError(
                    f"run {run_id} not terminal within {timeout}s (last status={last_status})")
            await asyncio.sleep(poll_interval)
