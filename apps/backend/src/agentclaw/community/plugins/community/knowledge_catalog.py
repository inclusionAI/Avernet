"""An unconfigured platform catalog fails closed."""

from agentclaw.community.kernel.knowledge import BotKnowledgeScope, KnowledgeError
from agentclaw.community.plugin_api.knowledge_catalog import KnowledgeCatalogPlugin


class UnavailableKnowledgeCatalog(KnowledgeCatalogPlugin):
    def list_in_space(
        self, *, space_id: int, actor_id: str, cursor: str = "", limit: int = 20
    ) -> dict:
        raise KnowledgeError("KNOWLEDGE_CATALOG_UNAVAILABLE", 503)

    def list_authorized(
        self, *, scope: BotKnowledgeScope, space_id: int
    ) -> tuple[str, ...]:
        raise KnowledgeError("KNOWLEDGE_CATALOG_UNAVAILABLE", 503)
