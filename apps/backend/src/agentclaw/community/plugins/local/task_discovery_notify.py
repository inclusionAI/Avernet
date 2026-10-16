"""NullNotifyMessagesProvider — local (test/singlebox) NotifyMessagesProvider 实现.

Test/offline 空实现: ``send`` 恒返回 ``None``. 对齐 ``NoopNotifySender``:
继承 ``MockSeam`` 便于测试, 由 task_discovery DI 兜底绑定 (corp 未注入时).
"""
from __future__ import annotations

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.notify_sender import NotifyMessage
from agentclaw.community.plugin_api.task_discovery_notify import (
    NotifyMessagesProvider,
)
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="local/singlebox/test 无钉钉/Slack 通道; send 恒 None",
)
class NullNotifyMessagesProvider(MockSeam, NotifyMessagesProvider):
    """空实现: ``send`` 恒 None (通知链路 noop, 不阻断主流程)."""

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "markdown",
    ) -> str | None:
        return None


__all__ = ["NullNotifyMessagesProvider"]
