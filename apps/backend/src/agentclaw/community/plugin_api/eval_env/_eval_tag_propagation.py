"""EvalTagPropagationProtocol — 评测标签传播。

将评测 Header 注入/还原逻辑从生产代码中抽离，
确保评测路由标签在请求链路中正确传播。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class EvalTagPropagationProtocol(Plugin, Protocol):
    """评测标签传播 Plugin。

    管理评测路由标签（``X-Agentclaw-Default-Tag``、
    ``X-Eval-Id`` 等）在 HTTP Header 中的注入与还原，
    隔离评测标识传播逻辑。
    """

    def inject_eval_tag(
        self,
        *,
        headers: dict[str, str],
        default_tag: str,
    ) -> dict[str, str]:
        """向 Headers 中注入评测路由标签。

        Parameters
        ----------
        headers : dict[str, str]
            原始 Headers。
        default_tag : str
            评测路由标签。

        Returns
        -------
        dict[str, str]
            注入评测标签后的 Headers。
            Noop 实现返回原 Headers（不注入）。
        """
        ...

    def restore_eval_id(
        self,
        *,
        headers: dict[str, str],
    ) -> dict[str, str]:
        """从 Headers 中还原评测标识（逆操作）。

        Parameters
        ----------
        headers : dict[str, str]
            包含评测标签的 Headers。

        Returns
        -------
        dict[str, str]
            还原后的 Headers。
            Noop 实现返回原 Headers。
        """
        ...