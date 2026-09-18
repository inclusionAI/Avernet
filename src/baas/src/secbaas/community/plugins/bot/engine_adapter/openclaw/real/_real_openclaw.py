"""OpenClaw 引擎 adapter。

薄封装:``ws_path`` 返回 ``/api/openclaw/ws``;``session_consistency_key``
返回 ``agent:main:session:{run_id}:user:{user_id}`` (openclaw 的 agent:main:
前缀差异,原 plan_session_id 的 openclaw 分支下沉至此);``create_adapter_session``
从 planned_id 解析裸 key 后以 uuid 逻辑新建,返回 id 补 ``agent:main:`` 前缀。
"""

from __future__ import annotations

from typing import Any

from secbaas.community.logger import get_logger
from secbaas.community.spi.bot.engine_adapter import extract_session_key_from_planned_id

from ..._base import BaseEngineAdapter

logger = get_logger("core-bot-run")


class OpenClawAdapter(BaseEngineAdapter):
    """OpenClaw 引擎 adapter —— WS 路径 ``/api/openclaw/ws``，亲和键带 agent:main: 前缀。"""

    engine_type = "openclaw"
    _WS_PATH = "/api/openclaw/ws"

    def session_consistency_key(
        self,
        *,
        tc_bot_id: str,
        user_id: str,
        run_id: str,
    ) -> str:
        return f"agent:main:session:{run_id}:user:{user_id}"

    async def create_adapter_session(
        self,
        *,
        session_client: Any,
        planned_id: str,
        user_id: str,
        metadata: dict[str, Any],
        bot_id: str,
        session_pending: bool = True,
    ) -> tuple[str, bool]:
        """openclaw：pending 解析裸 key 以 uuid 新建（补前缀）；显式 id 复用。"""
        if not session_pending:
            logger.info(
                "Adapter session already exists: session_id=%s, reusing", planned_id
            )
            return planned_id, True
        key = extract_session_key_from_planned_id(planned_id)
        adapter_session = await session_client.create_session(
            title=metadata.get("title", None),
            user_id=user_id,
            agent_id=bot_id,
            uuid=key,
            model=metadata.get("model", None),
            engine=self.engine_type,
        )
        adapter_session_id = adapter_session.id
        if not adapter_session_id.startswith("agent:main:"):
            adapter_session_id = f"agent:main:{adapter_session_id}"
        logger.info("Adapter session created: session_id=%s", adapter_session_id)
        return adapter_session_id, False
