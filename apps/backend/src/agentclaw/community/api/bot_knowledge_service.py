"""Transport-independent Bot knowledge Service API."""

from agentclaw.community.core.bot_knowledge.protocols import (
    BotKnowledgePublicationSelectionProtocol,
    BotKnowledgeServiceProtocol,
)

__all__ = ["BotKnowledgeServiceProtocol", "BotKnowledgePublicationSelectionProtocol"]
