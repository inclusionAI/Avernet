"""Bot-publish-approval concern — test / singlebox binding (local)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.bot_publish_approval import BotPublishApprovalPlugin


class TestBotPublishApprovalModule(Module):
    """test / singlebox: local publish-directly strategy."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.local.bot_publish_approval import (
            LocalBotPublishApproval,
        )

        binder.bind(
            BotPublishApprovalPlugin, to=LocalBotPublishApproval, scope=singleton
        )
