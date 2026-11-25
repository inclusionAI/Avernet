"""DI bindings for the bot create/delete lifecycle extension seam and the
template-write extension seam.

The default for both is the no-op extension — an unextended runtime runs the
shared flows exactly as before. Overlays (corp) replace these bindings.
"""
from __future__ import annotations

from injector import Module, singleton

from agentclaw.community.plugin_api.bot_create_extension import (
    BotCreateExtension,
    NoopBotCreateExtension,
)
from agentclaw.community.plugin_api.bot_template_write_extension import (
    NoopTemplateWriteExtension,
    TemplateWriteExtension,
)


class BotExtensionModule(Module):
    def configure(self, binder) -> None:
        binder.bind(BotCreateExtension, to=NoopBotCreateExtension, scope=singleton)
        binder.bind(
            TemplateWriteExtension, to=NoopTemplateWriteExtension, scope=singleton
        )
