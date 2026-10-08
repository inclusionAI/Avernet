"""NoopEvalTagPropagation — 评测标签传播 Noop 实现。

评测功能关闭时：
- inject_eval_tag 返回原 headers（不注入）
- restore_eval_id 返回原 headers
"""

from __future__ import annotations

from agentclaw.community.plugin_api.eval_env import EvalTagPropagationProtocol
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="评测环境离线：Header 不注入/不还原，透传原值",
)
class NoopEvalTagPropagation(MockSeam, EvalTagPropagationProtocol):
    """评测标签传播的 Noop 实现。

    所有 Header 透传，不注入也不还原评测标签，
    等效于请求链路中无评测标识。
    """

    def inject_eval_tag(
        self,
        *,
        headers: dict[str, str],
        default_tag: str,
    ) -> dict[str, str]:
        """Noop：返回原 headers，不注入评测标签。"""
        return headers

    def restore_eval_id(
        self,
        *,
        headers: dict[str, str],
    ) -> dict[str, str]:
        """Noop：返回原 headers。"""
        return headers