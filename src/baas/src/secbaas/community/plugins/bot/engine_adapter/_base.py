"""Shared base for engine adapters (aicoding / hermes / claude_code / teclaw / openclaw).

`BaseEngineAdapter.create_adapter_session` 是统一的获取/物化入口：从
`planned_id` 解析裸 session key（非 planned 格式原样返回），以 key 作为 uuid
新建（引擎侧幂等）。`session_consistency_key` 默认返回通用亲和键格式（原
`plan_session_id` 的 else 分支下沉至此）；openclaw 的 `agent:main:` 前缀差异
由 `OpenClawAdapter`、teclaw 的探测-创建语义由 `TeClawAdapter` 各自覆写。

子类通过类属性 `engine_type` / `_WS_PATH` 定制标识与 WS 路径。
"""

from __future__ import annotations

from typing import Any

from secbaas.community.logger import get_logger
from secbaas.community.spi.bot.engine_adapter import extract_session_key_from_planned_id

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
        run_id: str,
    ) -> str:
        """默认：通用亲和键格式 ``agent:{tc_bot_id}:session:{run_id}:user:{user_id}``。

        原 plan_session_id 的 else 分支下沉至此（aicoding / hermes /
        claude_code / teclaw 共用）；openclaw 的 ``agent:main:`` 前缀差异由
        OpenClawAdapter 覆写。显式 session_id 由调用方前置处理。
        """
        return f"agent:{tc_bot_id}:session:{run_id}:user:{user_id}"

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
        """统一获取/物化 adapter session，返回 ``(adapter_session_id, is_reused)``。

        - ``session_pending=False``（显式 session_id，会话已存在）→ 直接复用
          不创建；teclaw 仍探测（TeClawAdapter 覆写，探测本身即存在性判断）；
        - ``session_pending=True``（plan 构造，未物化）→ 从 planned_id 解析
          裸 session key（``extract_session_key_from_planned_id``，非 planned
          格式原样返回），以 key 作为 uuid 走创建逻辑（引擎侧幂等）。
        """
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
        logger.info("Adapter session created: session_id=%s", adapter_session.id)
        return adapter_session.id, False
