import pytest
from agentclaw.community.kernel.knowledge import BotKnowledgeScope, KnowledgeError
from agentclaw.community.plugins.community.knowledge_catalog import (
    UnavailableKnowledgeCatalog,
)


@pytest.mark.parametrize("method", ["list_authorized", "list_in_space"])
def test_missing_catalog_fails_closed(method):
    catalog = UnavailableKnowledgeCatalog()
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_CATALOG_UNAVAILABLE"):
        if method == "list_in_space":
            catalog.list_in_space(space_id=1, actor_id="owner")
        else:
            catalog.list_authorized(
                scope=BotKnowledgeScope("bot", "owner", "dev"), space_id=1
            )


def test_local_plugin_mockseam_records_read_contract(world):
    from agentclaw.community.plugin_api.knowledge_catalog import KnowledgeCatalogPlugin

    catalog = world.get(KnowledgeCatalogPlugin)
    catalog.set_response("list_authorized", ("kb",))
    assert catalog.list_authorized(
        scope=BotKnowledgeScope("bot", "owner", "dev"), space_id=1
    ) == ("kb",)
    assert len(catalog.calls_to("list_authorized")) == 1
