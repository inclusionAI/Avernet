"""NotifyMessagesProvider — re-export 兼容层.

协议与空实现已迁移至 ``plugin_api.task_discovery_notify``:

- ``agentclaw.community.plugin_api.task_discovery_notify.NotifyMessagesProvider`` (Plugin Protocol)
- ``agentclaw.community.plugin_api.task_discovery_notify.NullNotifyMessagesProvider`` (空实现)

本文件仅为向后兼容保留 re-export, 供尚未迁移的 core/DI consumer
(``discovery_service`` / ``task_module`` / ``lifecycle`` / ``notify``)
继续按旧路径 import。新代码请直接 import ``plugin_api.task_discovery_notify``。
"""
from __future__ import annotations

from agentclaw.community.plugin_api.task_discovery_notify import (  # noqa: F401
    NotifyMessagesProvider,
    NullNotifyMessagesProvider,
)

__all__ = ["NotifyMessagesProvider", "NullNotifyMessagesProvider"]
