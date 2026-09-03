"""OpenApiBotAdapter:BaaS Open API 单 bot 派发(httpx async,对齐 send_bot_message.py)。

ensure_grant:GET allowed-bots → 缺则 POST grant(Cookie+Referer 登录态,非 Bearer)。
send_message:POST /openapi/v1/messages(Bearer)→ message_id(=run_id);顺带回包 session_id(BaaS 当前未返时前向兼容为 None,将来补字段自动落库)。
get_run:GET /openapi/v1/messages/{id}→ {status,result,error}(status 大小写不敏感)。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

import httpx

from agentclaw.community.core.task.task_runner.client.ports import (
    ApiKeyProvider,
    BotSendResult,
    OpenApiBotPort,
)


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
    pass


class OpenApiAuthError(OpenApiError):
    pass  # 401/403 grant 失败不可重试


class OpenApiBadRequestError(OpenApiError):
    pass  # 4xx 不重试


class OpenApiRateLimitError(OpenApiError):
    pass  # 429 可重试


class OpenApiServerError(OpenApiError):
    pass  # 5xx 可重试


class OpenApiTimeoutError(OpenApiError):
    pass


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


class OpenApiBotAdapter(
    OpenApiBotPort
):  # pragma: no cover — live BaaS OpenApi HTTP client; exercised by singlebox/corp acceptance / 联调, not CI LOCAL line coverage
    def __init__(
        self,
        keys: ApiKeyProvider,
        *,
        http_client: httpx.AsyncClient | None = None,
        ensure_grant: bool = False,
    ) -> None:
        self._k = keys
        self._client = http_client or httpx.AsyncClient(base_url=keys.base_url)
        # ensure_grant=False(默认):OOB 预授权模式,跳过 allowed-bots GET/grant,直进 send_message。
        # admin allowed-bots 端点只认 Human Cookie,corp 无 cookie 时 Bearer-only 打它会被 BaaS 判 500;
        # prod 假定 bot 已 OOB 预授权 → 默认跳过。需自查/grant 的(测试/联调)显式传 ensure_grant=True。
        self._ensure_grant = ensure_grant
        # 自建 client(http_client 未注入)需 pin 到首个 loop 复用;跨 loop 用一次性 client 隔离连接池。
        # 注入的 client 由调用方管理 loop 绑定,_owns_client=False 时不 pin/不重建。
        self._owns_client: bool = http_client is None
        self._client_loop: asyncio.AbstractEventLoop | None = None

    async def _aclose(self) -> None:
        await self._client.aclose()

    @contextlib.asynccontextmanager
    async def _client_for_current_loop(self):
        """取得「与当前 running loop 兼容」的 client(自查/写共享同一 adapter 的多 loop 场景)。

        httpx.AsyncClient 首次使用即绑定其所在 event loop,跨 loop 复用会抛 ``RuntimeError: bound to a
        different event loop``。生产里 harness/scheduler/recovery 经 ``asyncio.run``、HTTP 经
        ``new_event_loop`` 共同驱动同一 adapter,故:首个 loop pin 持久 client(保留连接池、同 loop 复用);
        其它 loop 发一次性 client,退出即 aclose(不污染 pinned)。注入的 client 由调用方管 loop 绑定,
        原样返回同一 client,永不重建。
        """
        if not self._owns_client:
            yield self._client
            return
        loop = asyncio.get_running_loop()
        if self._client_loop is None:
            self._client_loop = loop
        if self._client_loop is loop:
            yield self._client
            return
        temp = httpx.AsyncClient(
            base_url=self._client.base_url,
            headers=self._headers(),
            timeout=self._client.timeout,
        )
        try:
            yield temp
        finally:
            await temp.aclose()

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
            logger.info(
                "[task][openapi_bot] ensure_grant 跳过(OOB 预授权模式) bot_id=%s",
                bot_id,
            )
            return
        logger.info("[task][openapi_bot] ensure_grant bot_id=%s", bot_id)
        prefix = self._k.api_key_prefix or self._k.api_key[:_DEFAULT_KEY_PREFIX_LEN]
        logger.info(
            "[task][openapi_bot] >>> ensure_grant GET /api/v1/api-keys/%s/allowed-bots bot_id=%s base_url=%s",
            prefix,
            bot_id,
            self._k.base_url,
        )
        async with self._client_for_current_loop() as client:
            r = await client.get(
                f"/api/v1/api-keys/{prefix}/allowed-bots", headers=self._headers()
            )
        logger.info(
            "[task][openapi_bot] <<< ensure_grant GET allowed-bots status=%s body=%s",
            r.status_code,
            _resp_summary(r),
        )
        _map_status(r)
        allowed = (r.json().get("data") or {}).get("allowed_bots") or []
        if bot_id in allowed:
            logger.info(
                "[task][openapi_bot] ensure_grant bot_id=%s 已在 allowed_bots,跳过 grant",
                bot_id,
            )
            return
        logger.info(
            "[task][openapi_bot] >>> ensure_grant POST grant bot_id=%s (cookie_set=%s referer_set=%s)",
            bot_id,
            bool(self._k.cookie),
            bool(self._k.referer),
        )
        async with self._client_for_current_loop() as client:
            g = await client.post(
                f"/api/v1/api-keys/{prefix}/allowed-bots/grant",
                json={"bot_id": bot_id},
                headers={"Cookie": self._k.cookie, "Referer": self._k.referer},
            )
        logger.info(
            "[task][openapi_bot] <<< ensure_grant POST grant status=%s body=%s",
            g.status_code,
            _resp_summary(g),
        )
        if g.status_code in (401, 403):
            raise OpenApiAuthError(f"grant {g.status_code} {g.text}")
        _map_status(g)

    @property
    def api_key_prefix(self) -> str:
        """secbaas allowed-bots URL 路径段:显式 api_key_prefix 优先,否则回退 api_key 前 8 位。

        grant/revoke 与派发 claim_on JOIN 用同一前缀口径(单一公共 api-key,服务端持有)。"""
        return self._k.api_key_prefix or self._k.api_key[:_DEFAULT_KEY_PREFIX_LEN]

    async def grant(self, *, bcs_bot_id: str, cookie: str, referer: str) -> None:
        """前端发起的 grant:透传人类登录 Cookie/Referer 到 secbaas admin 端点 grant 一 Bot。

        admin allowed-bots 端点只认人类 Cookie(Bearer-only 被判 500),故 cookie/referer 取自入站请求头
        (per-request,不复用 self._k 静态值),Authorization 不带(Bearer 在 admin 端无意义)。幂等:对已
        已 granted 的 (api_key, bot_id) secbaas 跳过。401/403 → OpenApiAuthError。"""
        prefix = self.api_key_prefix
        logger.info(
            "[task][openapi_bot] >>> grant POST /api/v1/api-keys/%s/allowed-bots/grant bcs_bot_id=%s base_url=%s",
            prefix,
            bcs_bot_id,
            self._k.base_url,
        )
        async with self._client_for_current_loop() as client:
            g = await client.post(
                f"/api/v1/api-keys/{prefix}/allowed-bots/grant",
                json={
                    "bot_id": bcs_bot_id
                },  # secbaas body 字段名仍 bot_id,值=bcs_bot_id(real:entity)
                headers={"Cookie": cookie, "Referer": referer},
            )
        logger.info(
            "[task][openapi_bot] <<< grant status=%s body=%s",
            g.status_code,
            _resp_summary(g),
        )
        if g.status_code in (401, 403):
            raise OpenApiAuthError(f"grant {g.status_code} {g.text}")
        _map_status(g)

    async def revoke(self, *, bcs_bot_id: str, cookie: str, referer: str) -> None:
        """前端发起的 revoke:透传人类登录 Cookie/Referer 到 secbaas admin 端点撤销授权。

        关闭=真 revoke:直接调 secbaas ``/allowed-bots/revoke``(幂等,无本地表)。
        cookie/referer per-request;401/403 → OpenApiAuthError。"""
        prefix = self.api_key_prefix
        logger.info(
            "[task][openapi_bot] >>> revoke POST /api/v1/api-keys/%s/allowed-bots/revoke bcs_bot_id=%s base_url=%s",
            prefix,
            bcs_bot_id,
            self._k.base_url,
        )
        async with self._client_for_current_loop() as client:
            r = await client.post(
                f"/api/v1/api-keys/{prefix}/allowed-bots/revoke",
                json={"bot_id": bcs_bot_id},
                headers={"Cookie": cookie, "Referer": referer},
            )
        logger.info(
            "[task][openapi_bot] <<< revoke status=%s body=%s",
            r.status_code,
            _resp_summary(r),
        )
        if r.status_code in (401, 403):
            raise OpenApiAuthError(f"revoke {r.status_code} {r.text}")
        _map_status(r)

    async def send_message(
        self, *, bot_id: str, message: str, metadata: dict[str, Any]
    ) -> BotSendResult:
        logger.info(
            "[task][openapi_bot] >>> send_message POST /openapi/v1/messages bot_id=%s base_url=%s msg_len=%s",
            bot_id,
            self._k.base_url,
            len(message or ""),
        )
        async with self._client_for_current_loop() as client:
            r = await client.post(
                "/openapi/v1/messages",
                json={"bot_id": bot_id, "message": message},
                headers=self._headers(),
            )
        logger.info(
            "[task][openapi_bot] <<< send_message status=%s body=%s",
            r.status_code,
            _resp_summary(r),
        )
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
        logger.info(
            "[task][openapi_bot] send_message 结果 message_id(run_id)=%s session_id=%s",
            message_id,
            data.get("session_id"),
        )
        return BotSendResult(run_id=message_id, session_id=data.get("session_id"))

    async def get_run(self, run_id: str) -> dict[str, Any]:
        logger.info(
            "[task][openapi_bot] >>> get_run GET /openapi/v1/messages/%s base_url=%s",
            run_id,
            self._k.base_url,
        )
        async with self._client_for_current_loop() as client:
            r = await client.get(
                f"/openapi/v1/messages/{run_id}", headers=self._headers()
            )
        logger.info(
            "[task][openapi_bot] <<< get_run status=%s body=%s",
            r.status_code,
            _resp_summary(r),
        )
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

    def send_and_wait(
        self,
        *,
        bot_id: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        timeout: float = 180.0,
        poll_interval: float = 2.0,
    ) -> dict[str, Any]:
        """同步便捷接口:ensure_grant → send_message → 轮询 get_run 到终态(COMPLETED/FAILED)。

        在自有新事件循环上跑完整个 async 链路(对齐 run_execute 模式),供同步调用方一次拿回答。
        终态返回 get_run 的 data dict;超时(默认 180s=3 分钟)抛 OpenApiTimeoutError;
        grant 403 → OpenApiAuthError、5xx → OpenApiServerError 透传给同步调用方。
        """
        logger.info("[task][openapi_bot] <<< send_and_wait, bot_id=%s", bot_id)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self.send_and_wait_async(
                    bot_id=bot_id,
                    message=message,
                    metadata=metadata,
                    timeout=timeout,
                    poll_interval=poll_interval,
                )
            )
        finally:
            loop.close()

    async def send_and_wait_async(
        self,
        *,
        bot_id: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        timeout: float = 180.0,
        poll_interval: float = 2.0,
    ) -> dict[str, Any]:
        logger.info(
            "[task][openapi_bot] >>> send_and_wait_async bot_id=%s base_url=%s",
            bot_id,
            self._k.base_url,
        )
        await self.ensure_grant(bot_id)
        sent = await self.send_message(
            bot_id=bot_id, message=message, metadata=metadata or {}
        )
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
                    f"run {run_id} not terminal within {timeout}s (last status={last_status})"
                )
            await asyncio.sleep(poll_interval)
