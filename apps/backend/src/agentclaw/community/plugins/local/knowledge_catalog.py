"""Controlled local catalog with recorded calls; fail closed without overrides."""

from agentclaw.community.kernel.knowledge import BotKnowledgeScope
from agentclaw.community.plugin_api.knowledge_catalog import KnowledgeCatalogPlugin
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.community.knowledge_catalog import (
    UnavailableKnowledgeCatalog,
)
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="No external I/O; explicit test overrides only",
)
class LocalKnowledgeCatalog(MockSeam, KnowledgeCatalogPlugin):
    def __init__(
        self,
        grants: dict[tuple[str, str, str, int], tuple[str, ...]] | None = None,
    ) -> None:
        # None means no catalog configured (fail closed). An explicitly empty
        # fixture is a working local catalog with no grants. No network or DB.
        self._grants = grants

    def list_in_space(self, *, space_id, actor_id, cursor="", limit=20):
        return UnavailableKnowledgeCatalog().list_in_space(
            space_id=space_id, actor_id=actor_id, cursor=cursor, limit=limit
        )

    def list_authorized(self, *, scope: BotKnowledgeScope, space_id: int):
        if self._grants is not None:
            return self._grants.get(
                (scope.bot_id, scope.owner_id, scope.env, space_id), ()
            )
        return UnavailableKnowledgeCatalog().list_authorized(
            scope=scope, space_id=space_id
        )
