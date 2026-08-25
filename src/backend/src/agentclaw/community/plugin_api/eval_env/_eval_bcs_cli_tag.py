"""EvalBcsCliTagProtocol — 评测 BCS 出站标拦截注入。

将 outbound_rules 中 ``X-Agentclaw-Default-Tag`` Header 注入
逻辑及 eval_bcs_cli_tag 中的标推导逻辑抽离为独立 Plugin。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class EvalBcsCliTagProtocol(Plugin, Protocol):
    """评测 BCS 出站标拦截注入 Plugin。

    核心职责：
    1. 在已有 OutBoundOperationRule 上追加 ``X-Agentclaw-Default-Tag``
       Header（替代 outbound_rules.build_rule() 中的 default_tag 参数）。
    2. 推导评测路由标签（derive_eval_tag / derive_eval_id）。
    3. 在 Provider 交付时注入评测 Header。
    """

    def inject_eval_headers(
        self,
        *,
        outbound_rule: Any,
        default_tag: str,
    ) -> Any:
        """在已有 OutBoundOperationRule 上追加评测 Header。

        替代 ``outbound_rules.build_rule(..., default_tag=xxx)`` 的
        侵入式传参方式，改为在构建 rule 后后置注入评测 Header。

        Parameters
        ----------
        outbound_rule : OutBoundOperationRule
            已构建的出站规则对象。
        default_tag : str
            评测路由标签。

        Returns
        -------
        OutBoundOperationRule
            追加评测 Header 后的出站规则。
            无 default_tag 时返回原 rule（不修改）。
        """
        ...

    def resolve_target_tag(
        self,
        *,
        bot_id: str,
        device_props: dict[str, Any],
    ) -> str:
        """推导评测路由标签。

        Parameters
        ----------
        bot_id : str
            Bot 标识。
        device_props : dict[str, Any]
            设备属性（包含 ``AGENTCLAW_DEFAULT_TAG`` 键）。

        Returns
        -------
        str
            推导出的评测路由标签，无评测标签时返回空字符串。
        """
        ...

    def provider_deliver(
        self,
        *,
        rule: Any,
        default_tag: str,
    ) -> Any:
        """Provider 交付时注入评测 Header。

        Parameters
        ----------
        rule : OutBoundOperationRule
            原始出站规则。
        default_tag : str
            评测路由标签。

        Returns
        -------
        OutBoundOperationRule
            注入评测 Header 后的出站规则。
            无 default_tag 时返回原 rule。
        """
        ...