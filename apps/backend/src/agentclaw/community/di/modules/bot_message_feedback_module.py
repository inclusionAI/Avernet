"""DI bindings for the message feedback domain."""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.bot_message_feedback_service import (
    BotMessageFeedbackServiceProtocol,
)
from agentclaw.community.core.bot_message_feedback.service import BotMessageFeedbackService
from agentclaw.community.core.repository.implementations.bot_message_feedback.repo import (
    BotMessageFeedbackRepository,
)
from agentclaw.community.core.repository.protocols.bot_message_feedback import (
    BotMessageFeedbackRepositoryProtocol,
)


class BotMessageFeedbackModule(Module):
    """Production bindings for message feedback."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            BotMessageFeedbackRepositoryProtocol,
            to=BotMessageFeedbackRepository,
            scope=singleton,
        )
        binder.bind(
            BotMessageFeedbackRepository,
            to=BotMessageFeedbackRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def bot_message_feedback_service(
        self,
        service: BotMessageFeedbackService,
    ) -> BotMessageFeedbackServiceProtocol:
        return service
