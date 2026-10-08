"""DI bindings for general user feedback."""

from injector import Binder, Module, singleton

from agentclaw.community.core.feedback.service_protocol import FeedbackServiceProtocol
from agentclaw.community.core.feedback.services.feedback_service import FeedbackService
from agentclaw.community.core.repository.implementations.feedback import (
    FeedbackRepository,
)
from agentclaw.community.core.repository.protocols.feedback import (
    FeedbackRepositoryProtocol,
)


class FeedbackModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(FeedbackRepositoryProtocol, to=FeedbackRepository, scope=singleton)
        binder.bind(FeedbackServiceProtocol, to=FeedbackService, scope=singleton)
