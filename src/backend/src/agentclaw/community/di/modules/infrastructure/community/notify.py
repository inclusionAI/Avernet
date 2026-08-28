"""Notify-sender concern — community binding.

Binds ``CommunityNotifySender`` (log-only, no real delivery) for the
community/singlebox profile. 当钉钉凭证就绪（``TASK_DISCOVERY_DINGTALK_*`` /
回退 ``SINGLEBOX_DINGTALK_*`` + ``TASK_DISCOVERY_CARD_TEMPLATE_ID``）时，
绑定 ``DingTalkNotifySender``（装饰 ``CommunityNotifySender``：先日志再投递
钉钉交互卡片，两者同时进行）。Corp deployments bind ``DingTalkNotifySender``
via ``CorpNotifyModule`` instead.

同时 alias ``NotifyMessagesProvider`` → 同一实例，供 task_discovery 域注入
(``DiscoveryService`` / ``TaskDiscoveryLifecycle``)。
``NotifySenderPlugin`` 保持绑定，供 governance 域注入。
"""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.core.task.task_discovery.notify_messages_provider import (
    NotifyMessagesProvider,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import NotifySenderPlugin

logger = get_logger(__name__)


class CommunityNotifyModule(Module):
    """community: 始终包裹 DingTalkNotifySender — 凭证就绪时投递卡片，否则跳过。

    钉钉凭证来源优先级：运行时 holder（API 注入）> YAML holder > env > 空（skip）。
    """

    @singleton
    @provider
    def _notify_sender(self) -> NotifySenderPlugin:
        logger.debug("[task_discovery] → CommunityNotifyModule._notify_sender()")
        from agentclaw.community.di.modules.config_module import _block
        from agentclaw.community.plugins.community.notify_sender import (
            CommunityNotifySender,
            DingTalkNotifySender,
            DingTalkYamlHolder,
        )

        # 从 YAML ``user_config.task_discovery_dingtalk`` 块加载凭证到 holder，
        # 让 notify_sender._resolve 在 env变量之前优先检查 YAML。
        cfg = _block("task_discovery_dingtalk")
        if cfg:
            DingTalkYamlHolder.set(cfg)
            logger.info(
                "[community.notify] dingtalk YAML keys loaded: %s",
                sorted(k for k, v in cfg.items() if v),
            )
        else:
            logger.warning(
                "[community.notify] task_discovery_dingtalk YAML block NOT found — "
                "dingtalk credentials will rely on env vars or API holder only",
            )

        inner = CommunityNotifySender()
        logger.info(
            "[community.notify] Binding DingTalkNotifySender(CommunityNotifySender) "
            "(log + dingtalk interactive card if creds available)",
        )
        return DingTalkNotifySender(inner)

    @singleton
    @provider
    def _notify_messages_provider(
        self, sender: NotifySenderPlugin,
    ) -> NotifyMessagesProvider:
        logger.debug("[task_discovery] → CommunityNotifyModule._notify_messages_provider(sender=%s)", type(sender).__name__)
        """Alias ``NotifyMessagesProvider`` → 同一 ``NotifySenderPlugin`` 实例。

        task_discovery 域 (``DiscoveryService`` / ``TaskDiscoveryLifecycle``) 注入
        ``NotifyMessagesProvider`` 端口;governance 域继续注入 ``NotifySenderPlugin``。
        两者共享同一 sender 实例。
        """
        return sender  # type: ignore[return-value]