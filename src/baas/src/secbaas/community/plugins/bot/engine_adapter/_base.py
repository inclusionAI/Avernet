"""Shared base for engine adapters (aicoding / hermes / claude_code).

`BaseEngineAdapter` 复刻 `BaasBotService._get_or_create_adapter_session` 中
**非 teclaw / 非 openclaw** 的通用 else 分支语义：有 session_id 直接复用，否则经
`session_client.create_session(...)` 新建（不加 openclaw 的 `agent:main:` 前缀）。

子类通过类属性 `engine_type` / `_WS_PATH` 定制标识与 WS 路径；hermes / claude_code
覆写 `session_consistency_key` / `create_adapter_session` 扩展引擎特有行为。
"""

from __future__ import annotations

from typing import Any

from secbaas.community.logger import get_logger

logger = get_logger("core-bot-run")


class BaseEngineAdapter:
    """通用引擎 adapter 基类。"""

    engine_type: str = ""
    _WS_PATH: str = ""

    def ws_path(self) -> str:
        return self._WS_PATH

    def session_consistency_key(
        self,
        *,
        tc_bot_id: str,
        user_id: str,
        run_id: str | None,
        session_id: str | None = None,
    ) -> str | None:
        """默认：session_id 优先，否则无 device 亲和（aicoding 语义）。"""
        return session_id

    def build_session_id(
        self,
        *,
        tc_bot_id: str,
        user_id: str,
        run_id: str,
        session_id: str | None = None,
    ) -> str | None:
        """默认：引擎不支持确定性 session ID，返回 None。"""
        return None

    async def create_adapter_session(
        self,
        *,
        session_client: Any,
        session_id: str | None,
        user_id: str,
        metadata: dict[str, Any],
        bot_id: str,
        run_id: str | None,
    ) -> tuple[str, bool]:
        """复刻通用 else 分支：有 session_id 复用，否则新建（无前缀）。"""
        if session_id:
            logger.info(
                "Adapter session already exists: session_id=%s, reusing", session_id
            )
            return session_id, True
        adapter_session = await session_client.create_session(
            title=metadata.get("title", None),
            user_id=user_id,
            agent_id=bot_id,
            uuid=run_id,
            model=metadata.get("model", None),
            engine=self.engine_type,
        )
        adapter_session_id = adapter_session.id
        logger.info("Adapter session created: session_id=%s", adapter_session_id)
        return adapter_session_id, False
