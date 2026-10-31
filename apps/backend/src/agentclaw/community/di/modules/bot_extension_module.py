"""DI bindings for the bot create/delete lifecycle extension seam.

The default is the no-op extension — an unextended runtime runs the shared
flows exactly as before. Overlays (corp) replace this binding.
"""
from __future__ import annotations

from injector import Module, singleton

from agentclaw.community.plugin_api.bot_create_extension import (
    BotCreateExtension,
    NoopBotCreateExtension,
)


class BotExtensionModule(Module):
    def configure(self, binder) -> None:
        binder.bind(BotCreateExtension, to=NoopBotCreateExtension, scope=singleton)
