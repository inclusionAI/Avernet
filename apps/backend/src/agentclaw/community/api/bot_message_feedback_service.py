"""Service API re-export for bot-output message feedback.

The Protocol is defined and implemented in ``core/bot_message_feedback/`` and
re-exported here so adapters can import the contract from the service API layer
without crossing the core -> api boundary.
"""
from __future__ import annotations

from agentclaw.community.core.bot_message_feedback.service_protocol import (
    BotMessageFeedbackServiceProtocol,
)


__all__ = ["BotMessageFeedbackServiceProtocol"]
