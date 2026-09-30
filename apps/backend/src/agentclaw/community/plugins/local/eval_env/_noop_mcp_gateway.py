"""NoopEvalMcpGateway — 评测 MCP 网关访问控制 Noop 实现。

全放行策略：
- configure_policy 为空操作
- check_mcp_access 返回 True（全放行）
- intercept_write 返回 True（全放行）
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.eval_env import EvalMcpGatewayProtocol
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="评测环境离线：MCP 网关全放行",
)
class NoopEvalMcpGateway(MockSeam, EvalMcpGatewayProtocol):
    """评测 MCP 网关的 Noop 实现。

    全放行——无论 default_tag / mcp_id / operation 如何，
    均允许访问。
    """

    def configure_policy(
        self,
        *,
        default_tag: str,
        policy: dict[str, Any],
    ) -> None:
        """Noop：空操作。"""

    def check_mcp_access(
        self,
        *,
        default_tag: str,
        mcp_id: str,
    ) -> bool:
        """Noop：全放行。"""
        return True

    def intercept_write(
        self,
        *,
        default_tag: str,
        mcp_id: str,
        operation: str,
    ) -> bool:
        """Noop：全放行。"""
        return True