"""NoopEvalBcsCliTag — 评测 BCS 出站标拦截注入 Noop 实现。

评测功能关闭时：
- inject_eval_headers 返回原 rule（不修改）
- resolve_target_tag 返回空字符串
- provider_deliver 返回原 rule
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.eval_env import EvalBcsCliTagProtocol
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="评测环境离线：出站规则不注入评测标签",
)
class NoopEvalBcsCliTag(MockSeam, EvalBcsCliTagProtocol):
    """评测 BCS 出站标拦截注入的 Noop 实现。

    所有出站规则原样透传，不注入评测 Header，
    推导标签返回空字符串。
    """

    def inject_eval_headers(
        self,
        *,
        outbound_rule: Any,
        default_tag: str,
    ) -> Any:
        """Noop：返回原 rule，不修改。"""
        return outbound_rule

    def resolve_target_tag(
        self,
        *,
        bot_id: str,
        device_props: dict[str, Any],
    ) -> str:
        """Noop：返回空字符串。"""
        return ""

    def provider_deliver(
        self,
        *,
        rule: Any,
        default_tag: str,
    ) -> Any:
        """Noop：返回原 rule。"""
        return rule