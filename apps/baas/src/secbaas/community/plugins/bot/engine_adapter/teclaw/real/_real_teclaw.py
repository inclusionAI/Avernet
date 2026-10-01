"""TeClaw 引擎 adapter。

薄封装:``ws_path`` 命中 engine 侧 teclaw 专属 router ``/api/teclaw/ws``;
``session_consistency_key`` 返回 teclaw 专属亲和键
``agent:main:default:{run_id}:user:{user_id}``;``create_adapter_session``
不需要解析裸 key,先 ``get_session`` 探测 planned_id(sessionKey)是否存在,
存在复用,不存在以该 id 创建新会话。
"""

from __future__ import annotations

from typing import Any

from secbaas.community.logger import get_logger

from ..._base import BaseEngineAdapter

logger = get_logger("core-bot-run")


class TeClawAdapter(BaseEngineAdapter):
    """TeClaw 引擎 adapter —— WS 路径 ``/api/teclaw/ws``。"""

    engine_type = "teclaw"
    _WS_PATH = "/api/teclaw/ws"

    def session_consistency_key(
        self,
        *,
        tc_bot_id: str,
        user_id: str,
        run_id: str,
    ) -> str:
        """teclaw 亲和键格式：``agent:main:default:{run_id}:user:{user_id}``。"""
        return f"agent:main:default:{run_id}:user:{user_id}"

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
        """teclaw：先探测 planned_id（sessionKey）是否存在，不存在以该 id 创建。

        ``session_pending`` 仅作调用方语义标注（显式 id / plan 值）；teclaw
        恒走探测——探测本身即存在性判断，显式 id 未落库时同样能补建。
        """
        try:
            await session_client.get_session(planned_id, self.engine_type)
            logger.info(
                "Adapter session already exists: session_id=%s, reusing", planned_id
            )
            return planned_id, True
        except Exception as e:
            logger.info(
                "Adapter session not found: session_id=%s, error=%s, creating new",
                planned_id,
                e,
            )
        adapter_session = await session_client.create_session(
            title=metadata.get("title", None),
            user_id=user_id,
            model=metadata.get("model", None),
            engine=self.engine_type,
            agent_id=bot_id,
            session_id=planned_id,
        )
        adapter_session_id = adapter_session.id
        logger.info("Adapter session created: session_id=%s", adapter_session_id)
        return adapter_session_id, False
