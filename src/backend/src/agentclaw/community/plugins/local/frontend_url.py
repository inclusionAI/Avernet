"""NullFrontendUrlProvider — local (test/singlebox) FrontendUrlProvider 实现.

Test/offline 空实现: ``get()`` 恒返回空串, 降级不阻断. 对齐 ``NoopNotifySender``:
继承 ``MockSeam`` 便于测试, 由 task_discovery DI 兜底绑定 (corp 未注入时).
"""
from __future__ import annotations

from agentclaw.community.plugin_api.frontend_url import FrontendUrlProvider
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="local/singlebox/test 无 env-aware YAML; 回落构造默认地址",
)
class NullFrontendUrlProvider(MockSeam, FrontendUrlProvider):
    """空实现: ``get()`` 恒返回 "" (下游回落构造默认 ``http://localhost:8000``)."""

    def get(self) -> str:
        return ""


__all__ = ["NullFrontendUrlProvider"]
