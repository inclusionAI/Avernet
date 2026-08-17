"""Service publication projection tests for the unified Bot inventory."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_inventory.adapters.service_lifecycle import (
    ServiceLifecycleView,
)
from agentclaw.community.core.bot_inventory.types import BotAction, DisplayState
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)


NOW = datetime(2026, 8, 17, 12, 0, 0)


def record(
    record_id: int,
    source_bot_pk: int,
    source_bot_id: str,
    status: PublishStatus,
    version: int,
) -> BotPublishRecord:
    return BotPublishRecord(
        id=record_id,
        source_bot_pk=source_bot_pk,
        source_bot_id=source_bot_id,
        publish_bot_id=source_bot_id,
        name="Service Bot",
        owner_id="owner",
        status=status.value,
        version=version,
        env="dev",
        permission_owner="owner",
        gmt_create=NOW,
        gmt_modified=NOW,
    )


@pytest.mark.unit
def test_cards_are_batched_and_expand_each_service_bot(monkeypatch) -> None:
    monkeypatch.setattr(
        "agentclaw.community.core.bot_inventory.adapters.service_lifecycle.get_current_env",
        lambda: "dev",
    )
    repo = Mock()
    repo.list_by_source_bots.return_value = [
        record(5, 10, "service-1", PublishStatus.UPGRADED, 5),
        record(4, 10, "service-1", PublishStatus.DRAFT, 4),
        record(3, 10, "service-1", PublishStatus.RELEASED, 3),
        record(2, 10, "service-1", PublishStatus.RELEASED, 2),
        record(7, 11, "service-2", PublishStatus.SUCCESS, 7),
        # A row cannot cross from another source Bot merely by sharing a PK.
        record(8, 11, "other", PublishStatus.DRAFT, 8),
    ]
    view = ServiceLifecycleView(repo)

    result = view.cards_for_bots(
        bots=[
            {"id": 10, "bot_id": "service-1", "status": "ACTIVE"},
            {"id": 11, "bot_id": "service-2", "status": "ACTIVE"},
        ]
    )

    repo.list_by_source_bots.assert_called_once_with((10, 11), "dev")
    assert [card.publication_id for card in result["service-1"]] == [4, 3]
    assert result["service-1"][0].display_state is DisplayState.SERVICE_DRAFT
    assert BotAction.DELETE not in result["service-1"][0].actions
    assert result["service-2"][0].display_state is DisplayState.SERVICE_ONLINE
    assert result["service-2"][0].live_version == 7


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "state"),
    [
        (PublishStatus.BUILDING, DisplayState.SERVICE_STAGING),
        (PublishStatus.ONLINE_PUB, DisplayState.SERVICE_STAGING),
        (PublishStatus.FAILED, DisplayState.SERVICE_STAGING),
        (PublishStatus.RELEASED, DisplayState.SERVICE_OFFLINE),
    ],
)
def test_service_display_state_mapping(status, state) -> None:
    assert ServiceLifecycleView._display_state(status.value) is state


@pytest.mark.unit
def test_missing_publish_rows_keep_a_safe_read_only_card(monkeypatch) -> None:
    monkeypatch.setattr(
        "agentclaw.community.core.bot_inventory.adapters.service_lifecycle.get_current_env",
        lambda: "dev",
    )
    repo = Mock()
    repo.list_by_source_bots.return_value = []

    cards = ServiceLifecycleView(repo).cards_for_bots(
        bots=[{"id": 10, "bot_id": "service-1", "status": "ACTIVE"}]
    )["service-1"]

    assert cards[0].publication_id is None
    assert cards[0].actions == (BotAction.VIEW,)
