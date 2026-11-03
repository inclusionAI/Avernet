"""Service API: current application selection, no grant or publication authority."""

from abc import abstractmethod
from typing import Protocol, runtime_checkable
from agentclaw.community.kernel.knowledge import BotKnowledgeScope


@runtime_checkable
class BotKnowledgeServiceProtocol(Protocol):
    @abstractmethod
    def set_enabled(
        self,
        *,
        scope: BotKnowledgeScope,
        actor_id: str,
        kb_id: str,
        enabled: bool,
        expected_revision: int,
    ) -> dict: ...
    @abstractmethod
    def list_bindings(
        self,
        *,
        scope: BotKnowledgeScope,
        actor_id: str,
        cursor: int = 0,
        limit: int = 20,
    ) -> dict: ...
    @abstractmethod
    def list_enabled_current(self, scope: BotKnowledgeScope) -> tuple[str, ...]: ...
