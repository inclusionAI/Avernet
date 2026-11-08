"""Consumer ↔ Plugin conformance through the real injector-backed World."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.api.space_knowledge_service import (
    SpaceKnowledgeServiceProtocol,
)
from agentclaw.community.core.spaces.space_service_protocol import (
    SpaceAccessServiceProtocol,
)
from agentclaw.community.plugin_api.space_knowledge import (
    KnowledgeBaseResult,
    KnowledgeBaseState,
    SpaceKnowledgeError,
    SpaceKnowledgeProvider,
    SpaceKnowledgeSync,
    SyncedSpace,
)


def test_consumer_routes_through_current_sync_and_provider(world):
    access = Mock()
    access.require_space_member.return_value = (SimpleNamespace(id=7, env="dev"), None)
    sync = Mock()
    sync.get_context.return_value = sync.ensure_synced.return_value = SyncedSpace(
        "7", "dev", 9
    )
    provider = Mock()
    provider.create.return_value = KnowledgeBaseResult(
        KnowledgeBaseState("kb", "tenant", "Name", "", "creating", "actor", "actor"),
        True,
    )
    world.injector.binder.bind(SpaceAccessServiceProtocol, to=access)
    world.injector.binder.bind(SpaceKnowledgeSync, to=sync)
    world.injector.binder.bind(SpaceKnowledgeProvider, to=provider)
    result = world.get(SpaceKnowledgeServiceProtocol).create(
        space_id=7, actor_id="actor", request_id="stable", name="Name", description=""
    )
    assert result.state.kb_id == "kb" and result.accepted
    sync.ensure_synced.assert_called_once()
    provider.create.assert_called_once_with(
        space=SyncedSpace("7", "dev", 9),
        actor_id="actor",
        request_id="stable",
        name="Name",
        description="",
    )


def test_default_sync_explicitly_unavailable_and_no_provider_write(world):
    access = Mock()
    access.require_space_member.return_value = (SimpleNamespace(id=7, env="dev"), None)
    provider = Mock()
    world.injector.binder.bind(SpaceAccessServiceProtocol, to=access)
    world.injector.binder.bind(SpaceKnowledgeProvider, to=provider)
    with pytest.raises(SpaceKnowledgeError, match="^SPACE_SYNC_UNAVAILABLE$"):
        world.get(SpaceKnowledgeServiceProtocol).get(
            space_id=7, actor_id="actor", kb_id="kb"
        )
    provider.get.assert_not_called()


def test_list_consumer_routes_page_through_sync_and_provider(world):
    from agentclaw.community.plugin_api.space_knowledge import KnowledgeBasePage

    access = Mock()
    access.require_space_member.return_value = (SimpleNamespace(id=7, env="dev"), None)
    sync = Mock()
    sync.get_context.return_value = sync.ensure_synced.return_value = SyncedSpace(
        "7", "dev", 9
    )
    provider = Mock()
    expected = KnowledgeBasePage([], "kb-next")
    provider.list_knowledge_bases.return_value = expected
    world.injector.binder.bind(SpaceAccessServiceProtocol, to=access)
    world.injector.binder.bind(SpaceKnowledgeSync, to=sync)
    world.injector.binder.bind(SpaceKnowledgeProvider, to=provider)
    assert (
        world.get(SpaceKnowledgeServiceProtocol).list_knowledge_bases(
            space_id=7, actor_id="actor", page_size=2, cursor="kb-before"
        )
        == expected
    )
    assert access.require_space_member.call_count == 2
    sync.get_context.assert_called_once_with(space_id=7, environment="dev")
    sync.ensure_synced.assert_not_called()
    provider.list_knowledge_bases.assert_called_once_with(
        space=SyncedSpace("7", "dev", 9),
        actor_id="actor",
        page_size=2,
        cursor="kb-before",
    )
