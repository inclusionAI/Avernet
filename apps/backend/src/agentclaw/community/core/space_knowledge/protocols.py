"""Service API for Space-scoped canonical knowledge asset operations."""

from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.space_knowledge import (
    KnowledgeBasePage,
    KnowledgeBaseResult,
)


@runtime_checkable
class SpaceKnowledgeServiceProtocol(Protocol):
    def create(
        self,
        *,
        space_id: int,
        actor_id: str,
        request_id: str,
        name: str,
        description: str,
    ) -> KnowledgeBaseResult: ...

    def get(
        self, *, space_id: int, actor_id: str, kb_id: str
    ) -> KnowledgeBaseResult: ...

    def list_knowledge_bases(
        self, *, space_id: int, actor_id: str, page_size: int = 50, cursor: str = ""
    ) -> KnowledgeBasePage: ...
