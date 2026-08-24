"""评测环境 SPI Protocol — BaaS 侧服务提供者接口。

BaaS 不管理 EvalEnvLifecycle/EvalVersionSync/EvalTagPropagation
（这些在 OCB 侧），BaaS 侧需要：
1. EvalBindingResolverProtocol — 评测绑定解析
2. EvalConsistencyCheckProtocol — 评测一致性检查
3. EvalSessionLogProtocol — 评测会话日志
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EvalBindingResolverProtocol(Protocol):
    """评测绑定解析 Protocol."""

    def resolve_eval_binding(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
    ) -> int | None:
        ...

    def is_eval_env_enabled(self) -> bool:
        ...


@runtime_checkable
class EvalConsistencyCheckProtocol(Protocol):
    """评测一致性检查 Protocol."""

    def check_default_tag_consistency(
        self,
        *,
        binding_info: Any,
        chat_metadata: dict[str, Any],
    ) -> bool:
        ...


@runtime_checkable
class EvalSessionLogProtocol(Protocol):
    """评测会话日志 Protocol."""

    def log_eval_session(
        self,
        *,
        eval_id: str,
        bot_id: str,
        session_id: str,
        method: str,
    ) -> None:
        ...

    def enrich_chat_metadata(
        self,
        *,
        metadata: dict[str, Any],
        run_id: str,
    ) -> dict[str, Any]:
        ...

    def extract_eval_headers(
        self,
        *,
        metadata: dict[str, Any],
        x_eval_id: str | None,
        x_default_tag: str | None,
    ) -> dict[str, Any]:
        ...