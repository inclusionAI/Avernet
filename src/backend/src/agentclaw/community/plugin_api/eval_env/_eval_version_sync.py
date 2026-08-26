"""EvalVersionSyncProtocol — 评测版本同步。

将评测 Bot 的版本检查、同步及发布事件监听逻辑
从 EvalVersionSyncService 中抽离为独立 Plugin。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class EvalVersionSyncProtocol(Plugin, Protocol):
    """评测版本同步 Plugin。

    管理评测 Bot 与生产 Bot 之间的版本一致性，
    包括版本检查、同步及发布事件响应。
    """

    def check_version(
        self,
        *,
        bot_id: str,
        version: str,
    ) -> bool:
        """检查评测 Bot 版本是否与指定版本一致。

        Parameters
        ----------
        bot_id : str
            Bot 标识。
        version : str
            待检查的版本号。

        Returns
        -------
        bool
            版本一致返回 ``True``，否则 ``False``。
            Noop 实现始终返回 ``True``（不阻止）。
        """
        ...

    def sync_version(
        self,
        *,
        bot_id: str,
        version: str,
    ) -> None:
        """同步评测 Bot 到指定版本。

        Parameters
        ----------
        bot_id : str
            Bot 标识。
        version : str
            目标版本号。
        """
        ...

    def on_publish_event(
        self,
        *,
        bot_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """响应 Bot 发布事件，触发评测版本同步。

        Parameters
        ----------
        bot_id : str
            Bot 标识。
        event_type : str
            发布事件类型。
        payload : dict[str, Any]
            事件载荷。
        """
        ...