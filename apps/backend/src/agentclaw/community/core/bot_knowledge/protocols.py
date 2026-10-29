"""Service API: stage-aware application selection, never grant authority."""

from abc import abstractmethod
from typing import Protocol, runtime_checkable
from agentclaw.community.kernel.knowledge import (
    BotKnowledgeScope,
    KnowledgeSelectionStage,
)


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
        selection_stage: KnowledgeSelectionStage = "draft",
    ) -> dict: ...
    @abstractmethod
    def list_enabled_current(self, scope: BotKnowledgeScope) -> tuple[str, ...]: ...

    @abstractmethod
    def list_enabled(
        self, scope: BotKnowledgeScope, *, selection_stage: KnowledgeSelectionStage
    ) -> tuple[str, ...]: ...
