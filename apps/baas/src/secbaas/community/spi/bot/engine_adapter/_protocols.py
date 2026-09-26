"""BotEngineAdapter SPI — 引擎差异抽象契约。

定义 `BotEngineAdapter` Protocol：把随 engine_type 分叉的引擎差异（WS path 段、
device 亲和 key、adapter session 创建语义）收敛成可注册、可测试的扩展点。

## Context Boundary

- **上游消费者**：`core/service/bot_run/_baas_service.py`、`_runner.py`
  （经 `BotEngineAdapterRegistry` 在接缝处按 `registry.has(engine_type)` 分流调用；
  `plan_session_id` 纯委托 adapter 表达亲和键差异）。
- **实现方**：`plugins/bot/engine_adapter/{openclaw,teclaw,aicoding,hermes,claude_code}/`
  的 `real/` + `stub/`（noop/mock）。
- **Scope**：全部引擎（openclaw / teclaw / aicoding / hermes / claude_code）；
  亲和键差异：openclaw → `agent:main:session:`、teclaw → `agent:main:default:`、
  其余 → 基类默认通用格式。
- send/inject 在 service 内无引擎分叉，**不属于本 SPI**。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotEngineAdapter(Protocol):
    """引擎差异适配器契约（openclaw / teclaw / aicoding / hermes / claude_code）。

    实现方通过 4 个成员表达引擎差异，其余会话/消息编排仍由 `BaasBotService` 统一处理。
    """

    @property
    def engine_type(self) -> str:
        """引擎标识（如 ``"aicoding"``），与注册键一致。"""
        ...

    def ws_path(self) -> str:
        """引擎在 engine adapter 侧监听的 WS 路径段。

        - openclaw → ``"/api/openclaw/ws"``
        - teclaw → ``"/api/teclaw/ws"``
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
        run_id: str,
    ) -> str:
        """返回 device 亲和一致性哈希字符串（传给 `_wss_resolver` 的 ``device_affinity``）。

        语义为路由亲和字符串（**非**去重 tuple）。显式 ``session_id`` 由调用方
        （``plan_session_id``）前置短路处理，本方法仅在无显式 session_id 时被调用。

        - 基类默认（aicoding / hermes / claude_code）→
          ``f"agent:{tc_bot_id}:session:{run_id}:user:{user_id}"``
        - openclaw → ``f"agent:main:session:{run_id}:user:{user_id}"``
        - teclaw → ``f"agent:main:default:{run_id}:user:{user_id}"``
        """
        ...

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
        """统一获取/物化 adapter 侧 session，返回 ``(adapter_session_id, is_reused)``。

        ``planned_id`` 兼容显式 session_id 与 plan 阶段构造的 planned id，
        ``session_pending`` 区分两者：

        - ``session_pending=False``（显式 session_id，会话已存在）→ 基类直接
          复用不创建；teclaw 仍探测（探测本身即存在性判断）
        - ``session_pending=True``（plan 构造，未物化）→ 基类从 planned_id
          解析裸 session key（非 planned 格式原样返回），以 key 作为 uuid
          走创建逻辑（引擎侧幂等）；teclaw 探测 planned_id（sessionKey），
          不存在以该 id 创建

        Args:
            session_client: `AsyncSessionClient`（duck-typed，含 create_session/get_session）。
            planned_id: 显式 session_id 或 plan 阶段构造的完整 session id。
            user_id: 创建 session 时传给 adapter 的 user id。
            metadata: 会话元数据（title / model 等）。
            bot_id: teamclaw bot id / agent id。
            session_pending: planned_id 是否为未物化的 plan 值（True）或
                已存在的显式 session_id（False）。

        Raises:
            BotNotAvailableError: 引擎侧不可用（如 hermes 持久化超时）。
        """
        ...
