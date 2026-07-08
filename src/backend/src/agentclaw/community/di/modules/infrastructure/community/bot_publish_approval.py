"""Bot-publish-approval concern — community binding (publish directly)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.bot_publish_approval import BotPublishApprovalPlugin


class CommunityBotPublishApprovalModule(Module):
    """community: publish directly, no approval workflow."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.community.bot_publish_approval import (
            DirectPublishApproval,
        )

        binder.bind(
            BotPublishApprovalPlugin, to=DirectPublishApproval, scope=singleton
        )
