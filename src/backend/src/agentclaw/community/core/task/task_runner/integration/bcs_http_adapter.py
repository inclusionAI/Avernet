"""BcsHttpAdapter:自包含 httpx async BCS client(对齐 ocb BcsHttpClient HMAC 模式,不 import ocb)。

HMAC 头:X-ECB-Token/X-ECB-Timestamp/X-ECB-Signature;签串 f"{ts}{method}{path}"。
create_group 三态(chat/manager_worker/state_machine);state_machine 强制 start_initial_run=false。
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from agentclaw.community.core.task.task_runner.integration.bcs_token_provider import BcsTokenProvider


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
        self._client = http_client or httpx.AsyncClient(base_url=token.base_url)

    def _sign(self, method: str, path: str, ts: str) -> dict[str, str]:
        sig = hmac.new(self._t.secret.encode(), f"{ts}{method}{path}".encode(), hashlib.sha256).hexdigest()
        return {"X-ECB-Token": self._t.token, "X-ECB-Timestamp": ts, "X-ECB-Signature": sig}

    async def _req(self, method: str, path: str, *, json: dict | None = None,
                   idempotency_key: str | None = None, extra_headers: dict | None = None) -> httpx.Response:
        ts = str(int(time.time()))
        headers = self._sign(method, path, ts)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if extra_headers:
            headers.update(extra_headers)
        r = await self._client.request(method, path, json=json, headers=headers)
        _map_status(r)
        return r

    async def create_group(self, req: BcsCreateGroupRequest) -> BcsCreateGroupResult:
        body: dict[str, Any] = {"driver_bot": req.driver_bot, "participants": req.participants}
        is_sm = req.group_strategy == "state_machine" or req.collaboration_definition_yaml
        if is_sm:
            body["group_strategy"] = "state_machine"
            body["start_initial_run"] = False
            if req.collaboration_definition_yaml:
                body["collaboration_definition_yaml"] = req.collaboration_definition_yaml
            if req.participant_bindings:
                body["participant_bindings"] = req.participant_bindings
        elif req.group_strategy:
            body["group_strategy"] = req.group_strategy
        for opt in ("context", "topic", "service_spec", "originator", "visibility"):
            v = getattr(req, opt)
            if v is not None:
                body[opt] = v
        r = await self._req("POST", "/groups", json=body, idempotency_key=uuid.uuid4().hex)
        data = r.json()
        return BcsCreateGroupResult(
            group_id=data["group_id"], session_id=data.get("session_id"),
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
        r = await self._client.request("GET", path, params=params, headers=headers)
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
