"""Service-Bot evaluation DI bindings used by the community runner."""

from __future__ import annotations

from agentclaw.community.api.service_bot_eval import ServiceBotEvalServiceProtocol
from agentclaw.community.core.service_bot.services.service_bot_eval_service import (
    ServiceBotEvalService,
)


def test_service_bot_eval_protocol_exposes_the_concrete_service(test_injector) -> None:
    service = test_injector.get(ServiceBotEvalService)
    protocol = test_injector.get(ServiceBotEvalServiceProtocol)

    assert protocol is service
