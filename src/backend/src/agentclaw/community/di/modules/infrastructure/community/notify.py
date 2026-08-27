"""Notify-sender concern — community binding.

Binds ``CommunityNotifySender`` (log-only, no real delivery) for the
community/singlebox profile. 当钉钉凭证就绪（``TASK_DISCOVERY_DINGTALK_*`` /
回退 ``SINGLEBOX_DINGTALK_*`` + ``TASK_DISCOVERY_CARD_TEMPLATE_ID``）时，
绑定 ``DingTalkNotifySender``（装饰 ``CommunityNotifySender``：先日志再投递
钉钉交互卡片，两者同时进行）。Corp deployments bind ``DingTalkNotifySender``
via ``CorpNotifyModule`` instead.
"""
from __future__ import annotations

from injector import Module, provider, singleton

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
        from agentclaw.community.di.modules.config_module import _block
        from agentclaw.community.plugins.community.notify_sender import (
            CommunityNotifySender,
            DingTalkNotifySender,
            DingTalkYamlHolder,
        )

        # 从 YAML ``user_config.task_discovery_dingtalk`` 块加载凭证到 holder，
        # 让 notify_sender._resolve 在 env 变量之前优先检查 YAML。
        cfg = _block("task_discovery_dingtalk")
        if cfg:
            DingTalkYamlHolder.set(cfg)
            # frontend_url 用于 session_url 生成（钉钉卡片点击跳转的前端地址）
            frontend_url = cfg.get("frontend_url", "")
            if frontend_url:
                from agentclaw.community.core.task.task_discovery.session_initiator import (
                    FrontendUrlHolder,
                )
                FrontendUrlHolder.set(frontend_url)

        inner = CommunityNotifySender()
        logger.info(
            "[community.notify] Binding DingTalkNotifySender(CommunityNotifySender) "
            "(log + dingtalk interactive card if creds available)",
        )
        return DingTalkNotifySender(inner)