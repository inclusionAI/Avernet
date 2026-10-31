"""Conversation-only knowledge references, not Bot configuration access."""

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from agentclaw.community.kernel.knowledge import BotKnowledgeScope


@runtime_checkable
class ConversationKnowledgeServiceProtocol(Protocol):
    def list_for_bot(
        self,
        *,
        scope: BotKnowledgeScope,
        actor_id: str,
        request_headers: Mapping[str, str],
        cursor: int = 0,
        limit: int = 20,
    ) -> dict:
        """Read one page of ready, currently granted and enabled Bot knowledge.

        Requires native conversation access, not Editor or caller KB ACLs.
        Never grants direct access to assets and never changes selections or ACLs.
        No local enabled selections returns an empty terminal page without
        contacting the provider. Enabled selections retain provider error checks.
        Empty pages may have a next_cursor when disabled entries were filtered.
        """
        ...
