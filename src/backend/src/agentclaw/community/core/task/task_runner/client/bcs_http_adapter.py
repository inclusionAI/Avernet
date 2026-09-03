"""BcsHttpAdapter:自包含 httpx async BCS client(对齐 ocb BcsHttpClient HMAC 模式,不 import ocb)。

HMAC 头:X-ECB-Token/X-ECB-Timestamp/X-ECB-Signature;签串 f"{ts}{method}{path}"。
create_group 三态(chat/manager_worker/state_machine);state_machine 强制 start_initial_run=false。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from agentclaw.community.core.task.task_runner.client.bcs_token_provider import BcsTokenProvider


logger = logging.getLogger(__name__)


def _response_summary(resp: httpx.Response) -> str:
    """Return a bounded response summary without leaking auth headers or large bodies."""
    text = (resp.text or "").replace("\n", " ")
    return text[:500]


class BcsClientError(Exception):
    ...


class BcsServerError(BcsClientError):
    ...  # 5xx 可重试


class BcsClientRequestError(BcsClientError):
    ...  # 4xx 不重试


class BcsRateLimitError(BcsClientError):
    ...  # 429


class BcsTimeoutError(BcsClientError):
    ...


@dataclass
class BcsCreateGroupRequest:
    driver_bot: str
    participants: list[dict[str, Any]]
    group_strategy: str | None = None           # chat(省略)/manager_worker/state_machine
    context: str | None = None
    topic: str | None = None
    collaboration_definition_yaml: str | None = None
    participant_bindings: dict[str, Any] | None = None
    service_spec: dict[str, Any] | None = None
    start_initial_run: bool | None = None
    originator: str | None = None
    visibility: str | None = None
    opening_message: dict[str, Any] | None = None
    event_subscriptions: list[dict[str, Any]] | None = None    # 内联事件订阅(回调 webhook);BCS 把 CloudEvent 推到 sink.url
    caller_bot_token: str | None = None                        # driver-bot 的 session token(直读 bcs_bots.session_token);参考 ocb:作为 Authorization: Bearer 做 caller 身份
    routing_policy: dict[str, Any] | None = None               # 拉人/不发言投递策略,如 {"default_bot_final_delivery":"inject_observers"}(人类观察者场景)
    label: str | None = None                                   # 群显示标签
    master_bot: str | None = None                              # manager_worker 群的 master bot(=driver_bot/manager);与 ocb 拉群接口对齐,form_coop_group 注入


@dataclass
class BcsCreateGroupResult:
    group_id: str
    session_id: str | None = None
    run_id: str | None = None
    definition_ref: dict[str, Any] | None = None


def _map_status(resp: httpx.Response) -> None:
    if resp.status_code == 429:
        raise BcsRateLimitError(f"429 {resp.text}")
    if 400 <= resp.status_code < 500:
        raise BcsClientRequestError(f"{resp.status_code} {resp.text}")
    if resp.status_code >= 500:
        raise BcsServerError(f"{resp.status_code} {resp.text}")


class BcsHttpAdapter:  # pragma: no cover — live BCS HTTP client (HMAC signing + REST); exercised by singlebox/corp acceptance / 联调, not CI LOCAL line coverage
    def __init__(self, token: BcsTokenProvider, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._t = token
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(base_url=token.base_url)
        # An httpx AsyncClient/connection pool is not safe to share across
        # asyncio event loops. The task module has a FastAPI loop plus poller
        # and harness loops, so keep the owned client pinned to the first loop
        # and use a short-lived client whenever a different loop calls us.
        self._client_loop: asyncio.AbstractEventLoop | None = None

    @asynccontextmanager
    async def _client_for_current_loop(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield an AsyncClient that belongs to the current event loop."""
        current_loop = asyncio.get_running_loop()
        if not self._owns_client:
            # Injected clients are test/custom transport ownership; preserve
            # their lifecycle and behavior exactly as before.
            yield self._client
            return

        if self._client_loop is None:
            self._client_loop = current_loop
            yield self._client
            return

        if self._client_loop is current_loop:
            yield self._client
            return

        # Do not move the persistent pool to another loop. A per-call client
        # is safe here and is closed on the loop that created it.
        logger.warning(
            "[task][bcs_http] event loop changed; using isolated client previous_loop=%s current_loop=%s",
            id(self._client_loop),
            id(current_loop),
        )
        client = httpx.AsyncClient(base_url=self._t.base_url)
        try:
            yield client
        finally:
            await client.aclose()

    def _sign(self, method: str, path: str, ts: str) -> dict[str, str]:
        sig = hmac.new(self._t.secret.encode(), f"{ts}{method}{path}".encode(), hashlib.sha256).hexdigest()
        return {"X-ECB-Token": self._t.token, "X-ECB-Timestamp": ts, "X-ECB-Signature": sig}

    def task_callback_url(self) -> str:
        """任务回投目标 origin(scheme://netloc):BCS 把 state_machine.* 等事件 POST 回此 origin +
        ``_BCN_EVENT_CALLBACK_PATH``。值由 token provider 经 corp 注入(``CorpBcsTokenProvider.task_callback_url``,
        env-aware ``bcs_client.task_callback_url[_pre]``);社区/singlebox 默认空 → TaskExecutor 兜底
        ``api_base_url``(economy_governance 派生)。"""
        return str(getattr(self._t, "task_callback_url", "") or "")

    async def _req(self, method: str, path: str, *, json: dict | None = None,
                   idempotency_key: str | None = None, extra_headers: dict | None = None) -> httpx.Response:
        ts = str(int(time.time()))
        headers = self._sign(method, path, ts)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if extra_headers:
            headers.update(extra_headers)
        _t0 = time.monotonic()
        logger.info(
            "[task][bcs_http] >>> request method=%s path=%s base_url=%s json_keys=%s idempotency=%s",
            method, path, self._t.base_url, sorted((json or {}).keys()), bool(idempotency_key),
        )
        try:
            async with self._client_for_current_loop() as client:
                r = await client.request(method, path, json=json, headers=headers)
        except Exception:
            logger.exception("[task][bcs_http] <<< request transport failed method=%s path=%s elapsed_ms=%s",
                             method, path, int((time.monotonic() - _t0) * 1000))
            raise
        logger.info(
            "[task][bcs_http] <<< response method=%s path=%s status=%s elapsed_ms=%s body=%s",
            method, path, r.status_code, int((time.monotonic() - _t0) * 1000), _response_summary(r),
        )
        try:
            _map_status(r)
        except Exception:
            logger.exception(
                "[task][bcs_http] response rejected method=%s path=%s status=%s body=%s",
                method, path, r.status_code, _response_summary(r),
            )
            raise
        return r

    async def create_group(self, req: BcsCreateGroupRequest) -> BcsCreateGroupResult:
        body: dict[str, Any] = {"driver_bot": req.driver_bot, "participants": req.participants}
        is_sm = req.group_strategy == "state_machine" or req.collaboration_definition_yaml
        if is_sm:
            body["group_strategy"] = "state_machine"
            # 透传调用方(form_coop_group)的 start_initial_run;未设时默认 False(向后兼容)。
            # 不得硬编码 False —— state_machine + event_subscriptions 时 BCS 要求自动启动(groups.rs:627)。
            body["start_initial_run"] = req.start_initial_run if req.start_initial_run is not None else False
            if req.collaboration_definition_yaml:
                body["collaboration_definition_yaml"] = req.collaboration_definition_yaml
            if req.participant_bindings:
                body["participant_bindings"] = req.participant_bindings
            if req.opening_message:
                body["opening_message"] = req.opening_message
        elif req.group_strategy:
            body["group_strategy"] = req.group_strategy
        if req.event_subscriptions:
            body["event_subscriptions"] = req.event_subscriptions
        for opt in ("context", "topic", "service_spec", "originator", "visibility", "label", "routing_policy", "master_bot"):
            v = getattr(req, opt)
            if v is not None:
                body[opt] = v
        # 参考 ocb(http_client.py:254-257):driver-bot 的 session token 经 Authorization: Bearer 做 caller 身份,
        # BCS resolve_group_create_caller 据此把 caller 解析成 driver/originator bot(仅 HMAC X-ECB-* 无 caller)。
        extra_headers: dict[str, str] | None = None
        if req.caller_bot_token:
            extra_headers = {"Authorization": f"Bearer {req.caller_bot_token}"}
            if len(req.caller_bot_token) > 4:
                logger.info(f"create_group extra_headers with bot_token {req.driver_bot}={req.caller_bot_token[0:4]}")
            else:
                logger.error(f"create_group extra_headers with wrong bot_token {req.driver_bot}={req.caller_bot_token}")

        logger.info("[task][bcs_http_adapter] create_group body=%s", body)
        logger.info("[task][bcs_http_adapter] create_group event_subscriptions=%s", body.get("event_subscriptions"))
        r = await self._req("POST", "/groups", json=body, idempotency_key=uuid.uuid4().hex,
                           extra_headers=extra_headers)
        data = r.json()
        # 实 BCS 创建群响应键名为 ``id``(``group_detail_to_create_json`` groups.rs:2093 与 v1 legacy
        # v1_group_detail_to_legacy_create_json groups.rs:782 均 ``"id": result.group_id``),
        # 非早期假设的 ``group_id``。与 ``singlebox_bcs_adapter.create_group`` 对齐取 ``(group_id or id)``,
        # 缺则带 body 抛 KeyError 便于定位(此前 ``data["group_id"]`` 对预发 BCS 直接 KeyError)。
        group_id = data.get("group_id") or data.get("id")
        if not group_id:
            logger.error(f"create_group 响应缺群 id(group_id/id 均无): {data}")
            raise KeyError(f"create_group 响应缺群 id(group_id/id 均无): {data}")
        return BcsCreateGroupResult(
            group_id=group_id, session_id=data.get("session_id"),
            run_id=data.get("run_id"),
            definition_ref=data.get("definition_ref"),
        )

    async def create_session(self, group_id: str, *, bootstrap_prompt: str | None = None,
                             idempotency_key: str | None = None) -> str:
        body: dict[str, Any] = {}
        if bootstrap_prompt is not None:
            body["bootstrap_prompt"] = bootstrap_prompt
        r = await self._req("POST", f"/groups/{group_id}/sessions", json=body,
                            idempotency_key=idempotency_key or uuid.uuid4().hex)
        return r.json()["session_id"]

    async def get_group(self, group_id: str) -> dict[str, Any]:
        r = await self._req("GET", f"/groups/{group_id}")
        return r.json()

    async def get_session_messages(self, session_id: str, *, limit: int = 50,
                                   since_msg_id: str | None = None) -> list[Any]:
        path = f"/sessions/{session_id}/messages"
        ts = str(int(time.time()))
        headers = self._sign("GET", path, ts)
        params: dict[str, Any] = {"limit": limit}
        if since_msg_id:
            params["since_msg_id"] = since_msg_id
        logger.info("[task][bcs_http] >>> get_session_messages GET path=%s base_url=%s limit=%s since=%s",
                    path, self._t.base_url, limit, since_msg_id)
        _t0 = time.monotonic()
        async with self._client_for_current_loop() as client:
            r = await client.request("GET", path, params=params, headers=headers)
        logger.info("[task][bcs_http] <<< get_session_messages GET path=%s status=%s elapsed_ms=%s body=%s",
                    path, r.status_code, int((time.monotonic() - _t0) * 1000), _response_summary(r))
        _map_status(r)
        return r.json()

    async def start_state_machine_run(self, group_id, *, definition_yaml, definition_ref,
                                      session_id, input) -> str:
        body: dict[str, Any] = {"input": input}
        if definition_ref is not None:
            body["definition_ref"] = definition_ref
        if definition_yaml is not None:
            body["definition_yaml"] = definition_yaml
        if session_id is not None:
            body["session_id"] = session_id
        r = await self._req("POST", f"/groups/{group_id}/state-machine-runs", json=body,
                            idempotency_key=uuid.uuid4().hex)
        data = r.json()
        return (data.get("run") or data).get("run_id")

    async def get_state_machine_run(self, run_id: str) -> dict[str, Any]:
        r = await self._req("GET", f"/state-machine-runs/{run_id}")
        return r.json()

    async def validate_definition(self, definition_yaml: str) -> None:
        await self._req("POST", "/collaboration/definitions/validate", json={"yaml": definition_yaml})
