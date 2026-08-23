"""SingleboxBcsAdapter:本地 BCS(:21000)的 BcsClientPort 实现,继承 BcsHttpAdapter。

本地 BCS 与生产同 REST、``require_authentication=false`` → HMAC ``X-ECB-*`` 头被本地忽略,
故传输/鉴权/路径/请求体/错误映射全部继承 ``BcsHttpAdapter``,不重写;只在**响应形状与生产不一致处**
覆写做归一。prod ``BcsHttpAdapter`` 零改动。

已确认差异(已 override):
- ``create_group``:本地 ``group_detail_to_create_json`` 用 ``"id"`` 返回群 id(非生产的 ``"group_id"``),
  且不返 ``run_id``/``definition_ref``(SM 的 run 由 ``start_state_machine_run`` 另起)。覆写把 ``id``→``group_id``。

待 e2e 验证后再 override(本地响应 key 待 live 确认,不在此凭猜测映射):
- ``get_state_machine_run``/``start_state_machine_run``/``get_session_messages``/``get_group``:
  先继承 ``BcsHttpAdapter``;真跑 singlebox coop_group e2e 时若 translator 拿不到 ``status``/``output``/
  ``session.*``,据真实响应再覆写归一。

已覆写:
- ``list_bots_by_task_modes``:Singlebox 首次查询时幂等初始化本地 task Provider,再复用生产一致的
  provider roster 路由;显式配置 ``SINGLEBOX_BCS_PROVIDER_ID``/``SINGLEBOX_BCS_PROVIDER_ADMIN_TOKEN``
  时跳过初始化。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import (
    BcsClientError,
    BcsClientRequestError,
    BcsCreateGroupRequest,
    BcsCreateGroupResult,
    BcsHttpAdapter,
    BotTaskModeRoster,
    _response_summary,
)


logger = logging.getLogger(__name__)


class SingleboxBcsAdapter(BcsHttpAdapter):  # pragma: no cover — live singlebox local BCS (:21000) adapter; exercised by singlebox acceptance / 联调, not CI LOCAL line coverage
    """singlebox 本地 BCS 适配:继承 BcsHttpAdapter,仅覆写响应形状不一致处。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._provider_init_lock = asyncio.Lock()

    async def _ensure_task_provider(self) -> None:
        """Ensure a local task provider exists before roster queries.

        Singlebox has no platform bootstrap that pre-provisions the provider
        credentials used by ``/providers/{provider_id}/bots/by-task-modes``.
        Create it lazily once per adapter instance so E2E/test callers can use
        the same BCS roster path without a manual setup step. Explicit
        ``SINGLEBOX_BCS_PROVIDER_ID`` and ``SINGLEBOX_BCS_PROVIDER_ADMIN_TOKEN``
        always win and skip creation.
        """
        if self._t.provider_id and self._t.provider_admin_token:
            return

        async with self._provider_init_lock:
            if self._t.provider_id and self._t.provider_admin_token:
                return

            token = self._t
            state_file = Path(token.provider_state_file) if token.provider_state_file else None
            if state_file and state_file.exists():
                try:
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    state = {}
                if (
                    state.get("base_url") == token.base_url
                    and state.get("provider_name") == token.provider_name
                ):
                    provider_id = str(state.get("provider_id") or "").strip()
                    provider_admin_token = str(state.get("provider_admin_token") or "").strip()
                    if provider_id and provider_admin_token:
                        token.provider_id = provider_id
                        token.provider_admin_token = provider_admin_token
                        logger.info(
                            "[singlebox_bcs] reused task provider provider_id=%s",
                            provider_id,
                        )
                        return
            body = {
                "name": token.provider_name,
                "webhook_url": token.provider_webhook_url,
                "auth": {"mode": "static_bearer"},
                "protocol_version": "1.0",
                "coordination": {
                    "mode": "disabled",
                    "mcp_server": "",
                    "mcporter_command": "",
                },
            }
            headers = {
                "Content-Type": "application/json",
                "X-Mock-User-Id": token.provider_owner_id,
                "X-Mock-Staff-No": token.provider_owner_id,
            }
            logger.info(
                "[singlebox_bcs] initializing task provider name=%s owner=%s",
                token.provider_name,
                token.provider_owner_id,
            )
            async with self._client_for_current_loop() as client:
                response = await client.post("/providers", json=body, headers=headers)
            if response.status_code >= 400:
                raise BcsClientRequestError(
                    f"singlebox task provider initialization failed: "
                    f"{response.status_code} {_response_summary(response)}"
                )

            data = response.json()
            provider_id = str(data.get("provider_id") or "").strip()
            provider_admin_token = str(data.get("provider_admin_token") or "").strip()
            if not provider_id or not provider_admin_token:
                raise BcsClientError(
                    "singlebox task provider initialization returned no provider credentials"
                )
            token.provider_id = provider_id
            token.provider_admin_token = provider_admin_token
            if state_file:
                try:
                    state_file.parent.mkdir(parents=True, exist_ok=True)
                    state_file.write_text(
                        json.dumps(
                            {
                                "base_url": token.base_url,
                                "provider_name": token.provider_name,
                                "provider_id": provider_id,
                                "provider_admin_token": provider_admin_token,
                            },
                            ensure_ascii=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                except OSError:
                    logger.warning(
                        "[singlebox_bcs] unable to persist task provider state file=%s",
                        state_file,
                        exc_info=True,
                    )
            logger.info(
                "[singlebox_bcs] task provider initialized provider_id=%s",
                provider_id,
            )

    async def list_bots_by_task_modes(
        self,
        *,
        claim: bool | None = None,
        dream: bool | None = None,
        match: str = "any",
    ) -> list["BotTaskModeRoster"]:
        await self._ensure_task_provider()
        return await super().list_bots_by_task_modes(
            claim=claim,
            dream=dream,
            match=match,
        )

    async def create_group(self, req: BcsCreateGroupRequest) -> BcsCreateGroupResult:
        # 请求体与 BcsHttpAdapter.create_group 完全一致(SM/chat 分流 + 可选字段);不复用 super 是因
        # super 直接取 data["group_id"] 会 KeyError(本地返 "id")。
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
        elif req.group_strategy:
            body["group_strategy"] = req.group_strategy
        if req.event_subscriptions:
            body["event_subscriptions"] = req.event_subscriptions
        for opt in ("context", "topic", "service_spec", "originator", "visibility"):
            v = getattr(req, opt)
            if v is not None:
                body[opt] = v
        r = await self._req("POST", "/groups", json=body, idempotency_key=uuid.uuid4().hex)
        data = r.json()
        # 本地用 "id";生产用 "group_id"。取其一,向后兼容 prod。
        group_id = data.get("group_id") or data.get("id")
        if not group_id:
            raise KeyError(f"create_group 响应缺群 id: {data}")
        return BcsCreateGroupResult(
            group_id=group_id,
            session_id=data.get("session_id"),
            run_id=data.get("run_id"),
            definition_ref=data.get("definition_ref"),
        )
