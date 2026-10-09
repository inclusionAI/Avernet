"""NotifyMessagesProvider — task_discovery 通知消息发送窄端口 (Plugin Protocol).

A deliberately narrow port beside ``NotifySenderPlugin``: task discovery only
needs ``send(message, *, channel) -> str | None``, without the ``channels``
capability surface that a general notify plugin carries. Keep it separate so
task discovery consumers do not depend on the full sender contract.

Mirrors ``BcsBotTokenProvider``: community core declares only the neutral
Protocol + ``NullNotifyMessagesProvider``; the corp column binds
``CorpNotifyMessagesProvider`` (env-aware DingTalk credentials) via DI.

Rule 20 — every Plugin Protocol has ≥1 local + ≥1 prod impl:
- local : ``NullNotifyMessagesProvider`` (plugins/local) — ``send`` → None.
- prod  : ``CorpNotifyMessagesProvider`` (corp/plugins/prod) — DingTalk card.

``send`` MUST never raise — errors caught internally and logged, returning
``None`` (same Rule as ``NotifySenderPlugin.send``).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin
from agentclaw.community.plugin_api.notify_sender import NotifyMessage


@runtime_checkable
class NotifyMessagesProvider(Plugin, Protocol):
    """``send(message, *, channel) -> message_id`` 通知消息发送窄端口。

    Downstream 新代码应按本窄端口注入 (port 更窄 / 不依赖 ``NotifySenderPlugin``
    的 ``channels`` 字段)。
    """

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "markdown",
    ) -> str | None:
        """发通知。

        Args:
            message: Channel-agnostic notification payload。
            channel: 预期通道 (如 "markdown" / "tc_card")。

        Returns:
            ``str`` external message ID on success, ``None`` on failure.
            实现 MUST **never raise**。
        """
        ...


class NullNotifyMessagesProvider:
    """空实现 (singlebox/test/未配置): ``send`` 恒 None, 降级不阻断。

    Mirror ``NullBcsBotTokenProvider``: corp column 未注入时 fallback 到本类。
    """

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "markdown",
    ) -> str | None:
        return None


__all__ = ["NotifyMessagesProvider", "NullNotifyMessagesProvider"]
