"""EvalMcpGatewayProtocol — 评测 MCP 网关访问控制。

管理评测环境下的 MCP 资源访问策略，
一期全放行，后续按策略拦截。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class EvalMcpGatewayProtocol(Plugin, Protocol):
    """评测 MCP 网关访问控制 Plugin。

    一期功能：全放行（prod 和 noop 均不拦截）。
    后续版本可按 default_tag 配置细粒度访问策略。
    """

    def configure_policy(
        self,
        *,
        default_tag: str,
        policy: dict[str, Any],
    ) -> None:
        """为指定评测标签配置访问策略。

        Parameters
        ----------
        default_tag : str
            评测路由标签。
        policy : dict[str, Any]
            策略配置。
        """
        ...

    def check_mcp_access(
        self,
        *,
        default_tag: str,
        mcp_id: str,
    ) -> bool:
        """检查评测环境是否有权访问指定 MCP。

        Parameters
        ----------
        default_tag : str
            评测路由标签。
        mcp_id : str
            MCP 资源标识。

        Returns
        -------
        bool
            允许访问返回 ``True``。
            Noop 实现始终返回 ``True``（全放行）。
        """
        ...

    def intercept_write(
        self,
        *,
        default_tag: str,
        mcp_id: str,
        operation: str,
    ) -> bool:
        """拦截评测环境的 MCP 写操作。

        Parameters
        ----------
        default_tag : str
            评测路由标签。
        mcp_id : str
            MCP 资源标识。
        operation : str
            操作类型。

        Returns
        -------
        bool
            允许操作返回 ``True``。
            Noop 实现始终返回 ``True``（全放行）。
        """
        ...