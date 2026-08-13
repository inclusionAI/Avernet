"""OpenApiBotAdapter:BaaS Open API 单 bot 派发(httpx async,对齐 send_bot_message.py)。

ensure_grant:GET allowed-bots → 缺则 POST grant(Cookie+Referer 登录态,非 Bearer)。
send_message:POST /openapi/v1/messages(Bearer)→ message_id(=run_id)。
get_run:GET /openapi/v1/messages/{id}→ {status,result,error}(status 大小写不敏感)。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from agentclaw.community.core.task.task_runner.integration.ports import ApiKeyProvider, OpenApiBotPort


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


class OpenApiBotAdapter(OpenApiBotPort):
    def __init__(self, keys: ApiKeyProvider, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._k = keys
        self._client = http_client or httpx.AsyncClient(base_url=keys.base_url)

    async def _aclose(self) -> None:
        await self._client.aclose()

    async def ensure_grant(self, bot_id: str) -> None:
        prefix = self._k.api_key_prefix
        r = await self._client.get(f"/api/v1/api-keys/{prefix}/allowed-bots",
                                   headers={"Authorization": f"Bearer {self._k.api_key}"})
        _map_status(r)
        allowed = (r.json().get("data") or {}).get("allowed_bots") or []
        if bot_id in allowed:
            return
        g = await self._client.post(f"/api/v1/api-keys/{prefix}/allowed-bots/grant",
                                    json={"bot_id": bot_id},
                                    headers={"Cookie": self._k.cookie, "Referer": self._k.referer})
        if g.status_code in (401, 403):
            raise OpenApiAuthError(f"grant {g.status_code} {g.text}")
        _map_status(g)

    async def send_message(self, *, bot_id: str, message: str, metadata: dict[str, Any]) -> str:
        r = await self._client.post("/openapi/v1/messages",
                                    json={"bot_id": bot_id, "message": message},
                                    headers={"Authorization": f"Bearer {self._k.api_key}"})
        _map_status(r)
        return (r.json().get("data") or {}).get("message_id")

    async def get_run(self, run_id: str) -> dict[str, Any]:
        r = await self._client.get(f"/openapi/v1/messages/{run_id}",
                                   headers={"Authorization": f"Bearer {self._k.api_key}"})
        _map_status(r)
        return r.json().get("data") or {}

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
        run_id = await self.send_message(bot_id=bot_id, message=message, metadata=metadata or {})
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
