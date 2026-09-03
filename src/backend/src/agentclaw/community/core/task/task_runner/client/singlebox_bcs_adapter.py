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

任务模式候选查询经 ``BcnService``(复用统一 provider 身份,``GET /providers/{provider_id}/bots/by-task-modes``)提供,不再继承 ``BcsHttpAdapter``。
"""
from __future__ import annotations

import uuid
from typing import Any

from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsCreateGroupRequest, BcsCreateGroupResult, BcsHttpAdapter,
)


class SingleboxBcsAdapter(BcsHttpAdapter):  # pragma: no cover — live singlebox local BCS (:21000) adapter; exercised by singlebox acceptance / 联调, not CI LOCAL line coverage
    """singlebox 本地 BCS 适配:继承 BcsHttpAdapter,仅覆写响应形状不一致处。"""

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
        for opt in ("context", "topic", "service_spec", "originator", "visibility", "label", "routing_policy", "master_bot"):
            v = getattr(req, opt)
            if v is not None:
                body[opt] = v
        # 与 BcsHttpAdapter.create_group 一致:透传 driver-bot Bearer(参考 ocb);本地 BCS 忽略鉴权,无害。
        extra_headers: dict[str, str] | None = None
        if req.caller_bot_token:
            extra_headers = {"Authorization": f"Bearer {req.caller_bot_token}"}
        r = await self._req("POST", "/groups", json=body, idempotency_key=uuid.uuid4().hex,
                           extra_headers=extra_headers)
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
