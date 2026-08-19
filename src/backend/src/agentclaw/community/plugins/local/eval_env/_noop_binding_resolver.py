"""NoopEvalBindingResolver — 评测绑定解析 Noop 实现。

评测功能关闭时：
- resolve_eval_binding 返回 None（降级走生产）
- resolve_default_binding 返回 None
"""

from __future__ import annotations

from agentclaw.community.plugin_api.eval_env import EvalBindingResolverProtocol
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="评测环境离线：绑定解析返回 None，降级走生产",
)
class NoopEvalBindingResolver(MockSeam, EvalBindingResolverProtocol):
    """评测绑定解析的 Noop 实现。

    返回 ``None`` 使调用方降级到生产 binding 路径，
    等效于 ``_is_eval_env_enabled() == False`` 时的行为。
    """

    def resolve_eval_binding(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
        default_tag: str,
    ) -> int | None:
        """Noop：返回 None，降级走生产。"""
        return None

    def resolve_default_binding(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
        lifecycle_stage: str = "online",
    ) -> int | None:
        """Noop：返回 None。"""
        return None