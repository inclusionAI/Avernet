"""GrtChatModule — production singleton for the grt_chat module.

``GrtChatService.__init__`` uses ``@inject`` to receive ``BotRepository``
and ``DeviceBindingRepository``; a ``configure`` self-binding is enough
for the injector to construct it.
"""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.core.grt_chat.services.grt_chat_service import GrtChatService


class GrtChatModule(Module):
    """Production bindings for the grt_chat module."""

    def configure(self, binder: Binder) -> None:
        binder.bind(GrtChatService, to=GrtChatService, scope=singleton)
