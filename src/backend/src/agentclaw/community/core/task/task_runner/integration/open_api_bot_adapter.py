"""OpenApiBotAdapter:BaaS Open API 单 bot 派发(httpx async,对齐 send_bot_message.py)。

ensure_grant:GET allowed-bots → 缺则 POST grant(Cookie+Referer 登录态,非 Bearer)。
send_message:POST /openapi/v1/messages(Bearer)→ message_id(=run_id);顺带回包 session_id(BaaS 当前未返时前向兼容为 None,将来补字段自动落库)。
get_run:GET /openapi/v1/messages/{id}→ {status,result,error}(status 大小写不敏感)。
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

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
    def __init__(self, keys: ApiKeyProvider, *, http_client: httpx.AsyncClient | None = None,
                 ensure_grant: bool = False) -> None:
        self._k = keys
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(base_url=keys.base_url)
        # httpx AsyncClient/connection pool 在跨 asyncio 事件循环上不安全:task 模块有 FastAPI loop +
        # harness/poller/scheduler 多个 loop(见 BcsHttpAdapter 同款问题)。把自建 client pin 到首个 loop,
        # 其它 loop 调用时在 _client_for_current_loop 内用一次性 client(在其创建的 loop 上 aclose,无泄漏)。
        # 注入的 client(测试/MockTransport)由调用方管理 loop 绑定,不重建。
        self._client_loop: asyncio.AbstractEventLoop | None = None
        # ensure_grant=False(默认):OOB 预授权模式,跳过 allowed-bots GET/grant,直进 send_message。
        # admin allowed-bots 端点只认 Human Cookie,corp 无 cookie 时 Bearer-only 打它会被 BaaS 判 500;
        # prod 假定 bot 已 OOB 预授权 → 默认跳过。需自查/grant 的(测试/联调)显式传 ensure_grant=True。
        self._ensure_grant = ensure_grant

    @asynccontextmanager
    async def _client_for_current_loop(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield an AsyncClient that belongs to the current event loop.

        httpx.AsyncClient 的连接池(httpcore/anyio 的 asyncio 原语)在首次使用时绑定到当前 loop,跨 loop
        复用会抛 ``RuntimeError: ... is bound to a different event loop``。本适配器被编排核在多个 loop 上驱动
        (FastAPI loop + harness/poller/scheduler loop,见 BcsHttpAdapter 同款):把自建 client pin 到首个 loop,
        其它 loop 调用时用一次性 client 并在它创建的 loop 上 aclose(安全、无泄漏)。注入的 client 由调用方管理。
        """
        current_loop = asyncio.get_running_loop()
        if not self._owns_client:
            yield self._client
            return
        if self._client_loop is None:
            self._client_loop = current_loop
            yield self._client
            return
        if self._client_loop is current_loop:
            yield self._client
            return
        logger.warning(
            "[task][openapi_bot] event loop changed; using isolated client previous_loop=%s current_loop=%s",
            id(self._client_loop), id(current_loop),
        )
        client = httpx.AsyncClient(base_url=self._k.base_url)
        try:
            yield client
        finally:
            await client.aclose()

    async def _aclose(self) -> None:
        # 仅在自建 client pin 的 loop(== 当前 running loop)上 close;跨 loop 时由 _client_for_current_loop
        # 用一次性 client 自行 close,此处的持久 client 留待其归属 loop 关闭。注入的 client 由调用方管理生命周期。
        if self._owns_client and self._client_loop is not asyncio.get_running_loop():
            return
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        """请求头:Bearer + 可选 Cookie/Referer。

        真实 ACE 网关后的 host(``agentclaw-*`` / ``secbaas-*``)除 Bearer 外还需登录 Cookie(+ Referer)
        才能过 ACE;否则 ACE 回 HTTP 200 的 USER_NOT_LOGIN 登录门(无业务 data),被误当成功而 run_id=None。
        本地 singlebox 与 service-to-service(``CorpApiKeyProvider`` cookie/referer 空)不加 → 行为不变。
        """
        h: dict[str, str] = {"Authorization": f"Bearer {self._k.api_key}"}
        if self._k.cookie:
            h["Cookie"] = self._k.cookie
        if self._k.referer:
            h["Referer"] = self._k.referer
        return h

    async def ensure_grant(self, bot_id: str) -> None:
        if not self._ensure_grant:
            logger.info("[task][openapi_bot] ensure_grant 跳过(OOB 预授权模式) bot_id=%s", bot_id)
            return
        logger.info("[task][openapi_bot] ensure_grant bot_id=%s", bot_id)
        prefix = self._k.api_key_prefix or self._k.api_key[:_DEFAULT_KEY_PREFIX_LEN]
        logger.info("[task][openapi_bot] >>> ensure_grant GET /api/v1/api-keys/%s/allowed-bots bot_id=%s base_url=%s",
                    prefix, bot_id, self._k.base_url)
        async with self._client_for_current_loop() as client:
            r = await client.get(f"/api/v1/api-keys/{prefix}/allowed-bots",
                                 headers=self._headers())
        logger.info("[task][openapi_bot] <<< ensure_grant GET allowed-bots status=%s body=%s",
                    r.status_code, _resp_summary(r))
        _map_status(r)
        allowed = (r.json().get("data") or {}).get("allowed_bots") or []
        if bot_id in allowed:
            logger.info("[task][openapi_bot] ensure_grant bot_id=%s 已在 allowed_bots,跳过 grant", bot_id)
            return
        logger.info("[task][openapi_bot] >>> ensure_grant POST grant bot_id=%s (cookie_set=%s referer_set=%s)",
                    bot_id, bool(self._k.cookie), bool(self._k.referer))
        async with self._client_for_current_loop() as client:
            g = await client.post(f"/api/v1/api-keys/{prefix}/allowed-bots/grant",
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
        async with self._client_for_current_loop() as client:
            r = await client.post("/openapi/v1/messages",
                                  json={"bot_id": bot_id, "message": message},
                                  headers=self._headers())
        logger.info("[task][openapi_bot] <<< send_message status=%s body=%s",
                    r.status_code, _resp_summary(r))
        _map_status(r)
        payload = r.json()
        data = payload.get("data") or {}
        message_id = data.get("message_id")
        code = payload.get("code")
        # 业务信封校验:HTTP 200 但 code!=0 或无 message_id(如 ACE 登录门/未授权),若不拦截会 run_id=None,
        # 后续 get_run(None) 报误导 404 "Message not found: None"。校验后直抛,带 code/payload 便于定位。
        if (code is not None and code != 0) or not message_id:
            raise OpenApiError(
                f"send_message 业务失败 code={code} message_id={message_id!r} payload={payload}"
            )
        logger.info("[task][openapi_bot] send_message 结果 message_id(run_id)=%s session_id=%s",
                    message_id, data.get("session_id"))
        return BotSendResult(run_id=message_id, session_id=data.get("session_id"))

    async def get_run(self, run_id: str) -> dict[str, Any]:
        logger.info("[task][openapi_bot] >>> get_run GET /openapi/v1/messages/%s base_url=%s", run_id, self._k.base_url)
        async with self._client_for_current_loop() as client:
            r = await client.get(f"/openapi/v1/messages/{run_id}",
                                 headers=self._headers())
        logger.info("[task][openapi_bot] <<< get_run status=%s body=%s", r.status_code, _resp_summary(r))
        _map_status(r)
        payload = r.json()
        code = payload.get("code")
        if code is not None and code != 0:
            raise OpenApiError(f"get_run 业务失败 code={code} payload={payload}")
        return payload.get("data") or {}

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
        logger.info("[task][openapi_bot] <<< send_and_wait, bot_id=%s", bot_id)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self.send_and_wait_async(bot_id=bot_id, message=message, metadata=metadata,
                                     timeout=timeout, poll_interval=poll_interval))
        finally:
            loop.close()

    async def send_and_wait_async(self, *, bot_id: str, message: str,
                                   metadata: dict[str, Any] | None = None, timeout: float = 180.0,
                                   poll_interval: float = 2.0) -> dict[str, Any]:
        logger.info("[task][openapi_bot] >>> send_and_wait_async bot_id=%s base_url=%s", bot_id, self._k.base_url)
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
