"""Controlled local catalog with recorded calls; fail closed without overrides."""

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
class LocalKnowledgeCatalog(MockSeam, UnavailableKnowledgeCatalog):
    def list_in_space(self, *, space_id, actor_id, cursor="", limit=20):
        return super().list_in_space(
            space_id=space_id, actor_id=actor_id, cursor=cursor, limit=limit
        )

    def list_authorized(self, *, scope, space_id):
        return super().list_authorized(scope=scope, space_id=space_id)
