"""Current application selection only. The knowledge platform owns grants."""

from agentclaw.community.core.bot_knowledge.protocols import BotKnowledgeServiceProtocol
from agentclaw.community.core.bot_knowledge.repository.store import (
    BotKnowledgeRepository,
)
from agentclaw.community.kernel.knowledge import (
    BotKnowledgeScope,
    KnowledgeError,
    require_knowledge_id,
)
from agentclaw.community.plugin_api.knowledge_catalog import KnowledgeCatalogPlugin


class BotKnowledgeService(BotKnowledgeServiceProtocol):
    def __init__(
        self, repository: BotKnowledgeRepository, catalog: KnowledgeCatalogPlugin
    ) -> None:
        self.repository, self.catalog = repository, catalog

    def set_enabled(
        self,
        *,
        scope: BotKnowledgeScope,
        actor_id: str,
        kb_id: str,
        enabled: bool,
        expected_revision: int,
    ) -> dict:
        for name, value in (
            ("actor_id", actor_id),
            ("kb_id", kb_id),
        ):
            require_knowledge_id(value, name)
        if (
            type(enabled) is not bool
            or type(expected_revision) is not int
            or expected_revision < 0
        ):
            raise ValueError("invalid mutation")
        bot = self.repository.get_bot(scope, actor_id=actor_id, for_edit=True)
        if enabled and kb_id not in self.catalog.list_authorized(
            scope=scope, space_id=bot["space_id"]
        ):
            raise KnowledgeError("KNOWLEDGE_NOT_GRANTED", 403)
        return self.repository.mutate(
            scope=scope,
            actor_id=actor_id,
            kb_id=kb_id,
            value=enabled,
            space_id=bot["space_id"],
            expected_revision=expected_revision,
        )

    def list_bindings(
        self,
        *,
        scope: BotKnowledgeScope,
        actor_id: str,
        cursor: int = 0,
        limit: int = 20,
    ) -> dict:
        if (
            type(cursor) is not int
            or cursor < 0
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ValueError("invalid page")
        return self.repository.list_bindings(
            scope=scope, actor_id=actor_id, cursor=cursor, limit=limit
        )

    def list_enabled_current(self, scope: BotKnowledgeScope) -> tuple[str, ...]:
        """Application selection, NEVER platform authorization evidence."""
        return self.repository.list_enabled_current(scope)
