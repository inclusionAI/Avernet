"""NoopEvalBindingResolver — 评测绑定解析 Stub 实现。

评测功能关闭时：
- resolve_eval_binding 返回 None（降级走生产）
- is_eval_env_enabled 返回 False
"""

from __future__ import annotations

from secbaas.community.spi.eval_env import EvalBindingResolverProtocol


class NoopEvalBindingResolver(EvalBindingResolverProtocol):
    """评测绑定解析的 Stub 实现。

    返回 ``None`` 使调用方降级到生产 binding 路径，
    等效于 ``EvalBindingResolverPlugin.is_eval_env_enabled() == False`` 时的行为。
    """

    def resolve_eval_binding(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
    ) -> int | None:
        """Stub：返回 None，降级走生产。"""
        return None

    def is_eval_env_enabled(self) -> bool:
        """Stub：始终返回 False。"""
        return False
