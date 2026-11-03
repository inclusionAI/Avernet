"""An unconfigured platform catalog fails closed."""

from agentclaw.community.kernel.knowledge import BotKnowledgeScope, KnowledgeError
from agentclaw.community.plugin_api.knowledge_catalog import KnowledgeCatalogPlugin
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl
from agentclaw.community.log import get_logger


logger = get_logger()


@plugin_impl(
    mode=Mode.PROD,
    rationale="Production catalog fails closed until a deployment configures an integration",
)
class UnavailableKnowledgeCatalog(KnowledgeCatalogPlugin):
    def list_in_space(
        self, *, space_id: int, actor_id: str, cursor: str = "", limit: int = 20
    ) -> dict:
        logger.warning("knowledge_catalog.unavailable operation=list_in_space reason=not_configured")
        raise KnowledgeError("KNOWLEDGE_CATALOG_UNAVAILABLE", 503)

    def list_authorized(
        self, *, scope: BotKnowledgeScope, space_id: int
    ) -> tuple[str, ...]:
        logger.warning("knowledge_catalog.unavailable operation=list_authorized reason=not_configured")
        raise KnowledgeError("KNOWLEDGE_CATALOG_UNAVAILABLE", 503)
