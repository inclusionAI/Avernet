"""Bindings for Admin Bot token-usage statistics."""

from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.core.bot_chat.token_usage_service import TokenUsageService


class BotTokenUsageModule(Module):
    """Bind the token usage aggregation service in every deployment profile."""

    def configure(self, binder: Binder) -> None:
        binder.bind(TokenUsageService, to=TokenUsageService, scope=singleton)
