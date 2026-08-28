"""NotifyMessagesProvider: task_discovery 的通知消息发送端口(core 只含中性端口 + 空实现)。

Mirrors ``BcsBotTokenProvider`` (``agentclaw.community.core.task.task_runner.integration.bcs_bot_token_provider``):

- community core/ 只定义 ``Protocol`` + ``NullNotifyMessagesProvider`` 空实现,
  core 不持厂商 SDK 凭证(钉钉/Slack/Email 等由 plugins 注入)。
- corp 由 ``CorpTaskIntegrationModule.get_notify_messages_provider`` 经 DI bind
  注入 ``CorpNotifyMessagesProvider`` — 构造期 env-aware 选 DingTalk 凭证 +
  ``FrontendUrlHolder`` 后,委托内部 ``DingTalkNotifySender(CommunityNotifySender)``
  发钉钉交互卡片。
- 未注入时(community / singlebox / test 列)默认 ``NullNotifyMessagesProvider``
  (``send`` 恒 None,降级不阻断)。

``CorpNotifyMessagesProvider`` 位于 ``src/agentclaw/corp/di/modules/infrastructure/corp/corp_task_integration.py``。
corp 的 ``CorpNotifyMessagesProvider`` 结构上也满足 ``NotifySenderPlugin``
``Protocol`` (有 ``send`` + ``channels``),向后兼容仍按 ``NotifySenderPlugin`` 注入
的旧 consumers (``DiscoveryService`` / ``TaskDiscoveryLifecycle``)。

参考: ``BcsBotTokenProvider`` / ``NullBcsBotTokenProvider``
(community 的 neutral port + null impl 习惯)。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.notify_sender import NotifyMessage


@runtime_checkable
class NotifyMessagesProvider(Protocol):
    """``send(message, *, channel) -> message_id`` 通知消息发送端口。

    Mirror of ``BcsBotTokenProvider``: community core/ 不持厂商 SDK,
    corp 由 ``CorpTaskIntegrationModule.get_notify_messages_provider`` 经 DI
    注入 ``CorpNotifyMessagesProvider`` (env-aware DingTalk credentials +
    holder setup + ``DingTalkNotifySender`` 委托 ``send``);未注入时 fallback
    ``NullNotifyMessagesProvider`` (恒 None,降级不阻断)。

    ``CorpNotifyMessagesProvider`` 结构上也满足 ``NotifySenderPlugin`` ``Protocol``
    (``send`` + ``channels``),backward-compat 仍按 ``NotifySenderPlugin`` 注入
    的旧 consumers。downstream 新代码建议按 ``NotifyMessagesProvider`` 注入
    (port 更 narrow / 不依赖 ``plugin_api`` 全部字段)。

    使用方式::

        provider: NotifyMessagesProvider = injector.get(NotifyMessagesProvider)
        msg_id = provider.send(
            NotifyMessage(title="...", body="...", recipient="..."),
            channel="tc_card",
        )
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
            channel: 预期通道(如 "markdown" / "tc_card")。

        Returns:
            ``str`` external message ID on success, ``None`` on failure。
            实现 MUST **never raise** — errors caught internally and logged
            (与 ``NotifySenderPlugin.send`` 同一 Rules/spec)。
        """
        ...


class NullNotifyMessagesProvider:
    """空实现(singlebox/test/未配置):恒返回 None,降级不阻断。

    Mirror ``NullBcsBotTokenProvider``: corp column 未注入时 fallback 到本类
    (由 community/task_module 负责绑默认);community / local column 默认拿本类。
    ``send`` 恒 None,通知链路 noop,不阻断 main flow。
    """

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "markdown",
    ) -> str | None:
        return None
