"""BotEngineAdapter SPI — 引擎差异抽象契约。

定义 `BotEngineAdapter` Protocol：把 `BaasBotService` 内随 engine_type 分叉的
引擎差异（WS path 段、device 亲和 key、adapter session 创建语义）收敛成可注册、
可测试的扩展点。

## Context Boundary

- **上游消费者**：`core/service/bot_run/_baas_service.py`（经 `BotEngineAdapterRegistry`
  在 3 处接缝按 `registry.has(engine_type)` 分流调用）。
- **实现方**：`plugins/bot/engine_adapter/{aicoding,hermes,claude_code}/{local,prod}`
  及各自 `_noop.py` / `_mock.py`。
- **Scope**：仅服务 `aicoding` / `hermes` / `claude_code` 三个新引擎；`openclaw` /
  `teclaw` 不经本 SPI，继续走 `BaasBotService` 的 `else` 原始分支（字节级不变）。
- send/inject 在 `BaasBotService` 内无引擎分叉，**不属于本 SPI**。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotEngineAdapter(Protocol):
    """引擎差异适配器契约（仅 aicoding / hermes / claude_code）。

    实现方通过 4 个成员表达引擎差异，其余会话/消息编排仍由 `BaasBotService` 统一处理。
    """

    @property
    def engine_type(self) -> str:
        """引擎标识（如 ``"aicoding"``），与注册键一致。"""
        ...

    def ws_path(self) -> str:
        """引擎在 engine adapter 侧监听的 WS 路径段。

        - aicoding → ``"/api/ws"``
        - hermes → ``"/api/hermes/ws"``
        - claude_code → ``"/api/claude_code/ws"``

        用于 `_resolve_ws_connection` 拼 path 与 `_build_base_url` strip 后缀。
        """
        ...

    def session_consistency_key(
        self,
        *,
        tc_bot_id: str,
        user_id: str,
        run_id: str | None,
        session_id: str | None = None,
    ) -> str | None:
        """返回 device 亲和一致性哈希字符串（传给 `_wss_resolver` 的 ``device_affinity``）。

        语义为路由亲和字符串（**非**去重 tuple）。``session_id`` 非空时优先返回它。

        - aicoding → ``None``
        - claude_code / hermes → ``f"agent:{tc_bot_id}:session:{run_id}:user:{user_id}"``
        """
        ...

    def build_session_id(
        self,
        *,
        tc_bot_id: str,
        user_id: str,
        run_id: str,
        session_id: str | None = None,
    ) -> str | None:
        """Construct a deterministic session ID without calling the engine.

        Returns the constructed session ID if this engine supports deterministic
        IDs, or ``None`` if the engine does not support them (and the caller
        should fall back to the synchronous session-creation path).

        Args:
            tc_bot_id: Teamclaw bot id / agent id.
            user_id: User id for session affinity.
            run_id: Run id used in the ID construction.
            session_id: Caller-supplied session id — returned as-is when present.
        """
        ...

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
        """获取或创建 adapter 侧 session，返回 ``(adapter_session_id, is_reused)``。

        封装 `BaasBotService._get_or_create_adapter_session` 中本引擎的语义分支。

        Args:
            session_client: `AsyncSessionClient`（duck-typed，含 create_session/get_session）。
            session_id: 已有 session id，None 表示新建。
            user_id: 创建 session 时传给 adapter 的 user id。
            metadata: 会话元数据（title / model 等）。
            bot_id: teamclaw bot id / agent id。
            run_id: 关联的 run id（作为 adapter uuid）。

        Raises:
            BotNotAvailableError: 引擎侧不可用（如 hermes 持久化超时、claude_code relay 离线）。
        """
        ...
