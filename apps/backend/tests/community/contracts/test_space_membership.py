"""Consumer contract: disabled sink has no writes; confirmed sink gates creation."""

from unittest.mock import Mock

import pytest

from agentclaw.community.core.repository.protocols.spaces import (
    SpaceKnowledgeSyncRepositoryProtocol,
)
from agentclaw.community.core.space_knowledge.membership_sync import (
    SpaceMembershipSynchronizer,
)
from agentclaw.community.plugin_api.space_membership import (
    SpaceMembershipSink,
    SpaceMembershipSnapshot,
)
from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError


def test_disabled_membership_consumer_never_activates(world):
    repository = Mock()
    world.injector.binder.bind(SpaceKnowledgeSyncRepositoryProtocol, to=repository)
    with pytest.raises(SpaceKnowledgeError, match="SPACE_SYNC_UNAVAILABLE"):
        world.get(SpaceMembershipSynchronizer).ensure_synced(
            space_id=7, environment="dev"
        )
    repository.activate.assert_not_called()


def test_consumer_applies_then_acknowledges_exact_snapshot(world):
    repository, sink = Mock(), Mock()
    sink.is_enabled.return_value = True
    repository.progress.side_effect = [(1, 0, "active"), (1, 1, "active")]
    snapshot = SpaceMembershipSnapshot("dev", 7, {"revision": 1})
    repository.next_snapshot.return_value = snapshot
    world.injector.binder.bind(SpaceKnowledgeSyncRepositoryProtocol, to=repository)
    world.injector.binder.bind(SpaceMembershipSink, to=sink)
    assert (
        world.get(SpaceMembershipSynchronizer)
        .ensure_synced(space_id=7, environment="dev")
        .revision
        == 1
    )
    sink.apply_snapshot.assert_called_once_with(snapshot)
    repository.acknowledge.assert_called_once()


def test_local_membership_sink_is_classified_and_never_acknowledges():
    from agentclaw.community.plugin_api.impl_registry import IMPL_REGISTRY, Flavor, Mode
    from agentclaw.community.plugins.unavailable.space_knowledge import (
        UnavailableSpaceMembershipSink,
    )

    entry = next(e for e in IMPL_REGISTRY if e.cls is UnavailableSpaceMembershipSink)
    assert entry.protocol is SpaceMembershipSink
    assert entry.mode is Mode.LOCAL and entry.flavor is Flavor.STUB
    sink = UnavailableSpaceMembershipSink()
    assert sink.is_enabled() is False
    with pytest.raises(SpaceKnowledgeError, match="^SPACE_SYNC_UNAVAILABLE$"):
        sink.apply_snapshot(SpaceMembershipSnapshot("dev", 7, {"revision": 1}))
