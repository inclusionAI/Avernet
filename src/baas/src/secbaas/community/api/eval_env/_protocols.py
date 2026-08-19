"""评测环境 Protocol 定义 — BaaS 侧 3 个 Plugin Protocol。

BaaS 不管理 EvalEnvLifecycle/EvalVersionSync/EvalTagPropagation
（这些在 OCB 侧），BaaS 侧需要：
1. EvalBindingResolverProtocol — 评测绑定解析
2. EvalConsistencyCheckProtocol — 评测一致性检查
3. EvalSessionLogProtocol — 评测会话日志
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ._models import EvalBindingInfo


@runtime_checkable
class EvalBindingResolverProtocol(Protocol):
    """评测绑定解析 Protocol。

    从原 ``_bot_binding_resolver`` 内联逻辑迁移，现已通过
    ``EvalBindingResolverProtocol`` Plugin 实现插拔式替换。
    """

    def resolve_eval_binding(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
    ) -> int | None:
        """解析评测环境的 binding_id。

        Parameters
        ----------
        bot_id : str
            Bot 标识。
        entity_id : str
            实体标识。
        env : str
            环境标识。

        Returns
        -------
        int | None
            评测 binding_id，无匹配时返回 ``None``（降级走生产）。
        """
        ...

    def is_eval_env_enabled(self) -> bool:
        """检查评测环境功能是否启用。

        Returns
        -------
        bool
            启用返回 ``True``，否则 ``False``。
            stub 实现始终返回 ``False``。
        """
        ...


@runtime_checkable
class EvalConsistencyCheckProtocol(Protocol):
    """评测一致性检查 Protocol。

    从 ``_baas_service`` 中的 eval 一致性检查逻辑迁移。
    """

    def check_default_tag_consistency(
        self,
        *,
        binding_info: Any,
        chat_metadata: dict[str, Any],
    ) -> bool:
        """检查 default_tag 一致性。

        Parameters
        ----------
        binding_info : BotBindingInfo
            当前 binding 信息。
        chat_metadata : dict[str, Any]
            会话 metadata。

        Returns
        -------
        bool
            一致返回 ``True``，否则 ``False``。
            stub 实现始终返回 ``True``（跳过检查）。
        """
        ...


@runtime_checkable
class EvalSessionLogProtocol(Protocol):
    """评测会话日志 Protocol。

    从 ``_runner`` 和 ``_bot_run_utils`` 中的
    eval 观测字段逻辑迁移。
    """

    def log_eval_session(
        self,
        *,
        eval_id: str,
        bot_id: str,
        session_id: str,
        method: str,
    ) -> None:
        """记录评测会话日志。

        Parameters
        ----------
        eval_id : str
            评测标识。
        bot_id : str
            Bot 标识。
        session_id : str
            会话标识。
        method : str
            调用方法名。
        """
        ...

    def enrich_chat_metadata(
        self,
        *,
        metadata: dict[str, Any],
        run_id: str,
    ) -> dict[str, Any]:
        """向会话 metadata 注入评测观测字段。

        Parameters
        ----------
        metadata : dict[str, Any]
            原始 metadata。
        run_id : str
            运行记录标识。

        Returns
        -------
        dict[str, Any]
            注入评测字段后的 metadata。
            stub 实现返回原 metadata。
        """
        ...

    def extract_eval_headers(
        self,
        *,
        metadata: dict[str, Any],
        x_eval_id: str | None,
        x_default_tag: str | None,
    ) -> dict[str, Any]:
        """从 HTTP Header 提取评测标识并注入 metadata。

        将 ``X-Eval-Id`` 和 ``X-Agentclaw-Default-Tag`` Header
        的值解析后注入到 metadata 中，供下游消费。

        Parameters
        ----------
        metadata : dict[str, Any]
            原始 metadata。
        x_eval_id : str | None
            X-Eval-Id Header 值。
        x_default_tag : str | None
            X-Agentclaw-Default-Tag Header 值。

        Returns
        -------
        dict[str, Any]
            注入评测 Header 信息的 metadata。
            stub 实现返回原 metadata。
        """
        ...