"""Unified PUBLISHED event delivery into durable Track Latest fanout."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from agentclaw.community.core.events.bus import (
    RequiredEventDeliveryError,
    get_event_bus,
    reset_event_bus,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    PublishedMaterializedSkillVersion,
)
from agentclaw.community.core.skill_center.services.track_latest_event_listener import (
    TrackLatestPublishedVersionListener,
)


def _published() -> PublishedMaterializedSkillVersion:
    return PublishedMaterializedSkillVersion(
        skill_version_id=11,
        skill_id=7,
        version_ordinal=2,
        status="PUBLISHED",
        skill_uuid="00000000-0000-4000-8000-000000000007",
        sc_version_number="2.0.0",
        sc_skill_id=70,
        sc_version_id=72,
        name="published",
        description=None,
        metadata_json='{"mcp_dependencies":[]}',
        published_at=datetime(2026, 8, 30, tzinfo=UTC),
    )


@pytest.fixture(autouse=True)
def _fresh_bus():
    reset_event_bus()
    yield
    reset_event_bus()


def test_bootstrap_is_idempotent_and_each_event_is_delivered_once() -> None:
    class _TrackLatest:
        def __init__(self) -> None:
            self.events = []

        def version_published(self, version) -> None:
            self.events.append(version)

    track_latest = _TrackLatest()
    listener = TrackLatestPublishedVersionListener(track_latest)

    asyncio.run(listener.bootstrap())
    asyncio.run(listener.bootstrap())
    get_event_bus().publish(_published())

    assert track_latest.events == [_published()]


def test_enqueue_failure_is_required_so_publication_task_can_redeliver() -> None:
    class _UnavailableTrackLatest:
        def version_published(self, _version) -> None:
            raise RuntimeError("task queue unavailable")

    listener = TrackLatestPublishedVersionListener(_UnavailableTrackLatest())
    asyncio.run(listener.bootstrap())

    with pytest.raises(RequiredEventDeliveryError):
        get_event_bus().publish(_published())
