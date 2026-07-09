"""Notify-sender concern — community binding (no-op).

Community ships no messaging channel, so notifications degrade to
``CommunityNotifySender`` (every send returns ``None``).
Corp binds the DingTalk sender instead
(``infrastructure/corp/notify.py``).
"""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.notify_sender import NotifySenderPlugin


class CommunityNotifyModule(Module):
    """community: no-op notification sender."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.community.notify_sender import (
            CommunityNotifySender,
        )

        binder.bind(NotifySenderPlugin, to=CommunityNotifySender, scope=singleton)