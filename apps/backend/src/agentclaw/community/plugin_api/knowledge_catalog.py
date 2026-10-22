"""Read-only platform catalog. Application selection cannot confer access."""

from typing import Protocol, runtime_checkable
from agentclaw.community.plugin_api.base import Plugin
from agentclaw.community.kernel.knowledge import BotKnowledgeScope


@runtime_checkable
class KnowledgeCatalogPlugin(Plugin, Protocol):
    def list_in_space(
        self, *, space_id: int, actor_id: str, cursor: str = "", limit: int = 20
    ) -> dict: ...
    def list_authorized(
        self, *, scope: BotKnowledgeScope, space_id: int
    ) -> tuple[str, ...]: ...
