"""LocalEvaluationGate——local 绑定：无外呼（IoC-free），放行。

本地开发没有门禁平台可连，策略恒为未命中；注册进 IMPL_REGISTRY 供
impl_registry 驱动的装配/契约测试发现（Rule 21）。
"""
from __future__ import annotations

from agentclaw.community.plugins.community.evaluation_gate import (
    AllowAllEvaluationGate,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="no I/O",
)
class LocalEvaluationGate(AllowAllEvaluationGate):
    """本地门禁 seam：策略未命中、发布放行。"""