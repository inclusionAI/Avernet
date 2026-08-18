"""Unit tests for BotInventoryService aggregation behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_inventory.adapters.noop_business_space import (
    NoopBusinessSpaceContext,
)
from agentclaw.community.core.bot_inventory.adapters.noop_service_lifecycle import (
    NoopServiceLifecyclePort,
)
from agentclaw.community.core.bot_inventory.services.bot_inventory_service import (
    BotInventoryService,
)
from agentclaw.community.core.bot_inventory.services.lifecycle_view import (
    BotLifecycleView,
)
from agentclaw.community.core.bot_inventory.types import (
    BotAction,
    DeployMode,
    DisplayState,
    ServiceLifecycleCard,
)


CLOUD = {
    "bot_id": "c1",
    "bot_name": "Cloud",
    "bot_desc": "cloud bot",
    "active_engine": "teclaw",
    "bot_type": "personal",
    "status": "ACTIVE",
    "owner_id": "u1",
}
LOCAL = {
    "bot_id": "l1",
    "bot_name": "Local",
    "bot_desc": "local bot",
    "active_engine": "openclaw",
    "bot_type": "desktop",
    "status": "OFFLINE",
    "owner_id": "u1",
}


@pytest.fixture
def service():
    bot = MagicMock()
    bot.list_bots_by_conditions.return_value = {"total": 1, "items": [CLOUD]}
    bot.get_bot.return_value = CLOUD
    desktop = MagicMock()
    desktop.list_user_bots.return_value = [LOCAL]
    return (
        BotInventoryService(
            bot_service=bot,
            desktop_service=desktop,
            business_space=NoopBusinessSpaceContext(),
            lifecycle_view=BotLifecycleView(NoopServiceLifecyclePort()),
        ),
        bot,
        desktop,
    )


@pytest.mark.unit
def test_list_items_combines_filters_and_paginates(service) -> None:
    inventory, _, _ = service

    items, total = inventory.list_items(
        owner_id="u1",
        space=None,
        keyword=None,
        engine=None,
        deploy_mode=None,
        page=1,
        page_size=10,
    )

    assert total == 2
    assert {item.bot_id for item in items} == {"c1", "l1"}


@pytest.mark.unit
def test_cloud_source_fetches_all_pages_for_exact_total(service) -> None:
    inventory, bot, desktop = service
    cloud_rows = [
        {**CLOUD, "bot_id": f"c{i:04d}", "bot_name": f"Cloud {i:04d}"}
        for i in range(1_200)
    ]

    def list_page(**kwargs):
        page = kwargs["page"]
        page_size = kwargs["page_size"]
        start = (page - 1) * page_size
        return {
            "total": len(cloud_rows),
            "items": cloud_rows[start : start + page_size],
        }

    bot.list_bots_by_conditions.side_effect = list_page
    desktop.list_user_bots.return_value = []

    items, total = inventory.list_items(
        owner_id="u1",
        space=None,
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.CLOUD,
        page=12,
        page_size=100,
    )

    assert total == len(cloud_rows)
    assert len(items) == 100
    assert items[0].bot_id == "c1100"
    assert bot.list_bots_by_conditions.call_count == 6


@pytest.mark.unit
def test_service_bot_expands_to_publication_cards_before_pagination() -> None:
    bot = MagicMock()
    service_row = {
        **CLOUD,
        "id": 10,
        "bot_id": "s1",
        "bot_name": "Service",
        "bot_type": "service",
    }
    bot.list_bots_by_conditions.return_value = {
        "total": 2,
        "items": [CLOUD, service_row],
    }
    desktop = MagicMock()
    desktop.list_user_bots.return_value = []
    lifecycle_port = MagicMock()
    lifecycle_port.cards_for_bots.return_value = {
        "s1": (
            ServiceLifecycleCard(
                publication_id=4,
                version=4,
                display_state=DisplayState.SERVICE_DRAFT,
                status="draft",
                actions=(BotAction.VIEW, BotAction.PUBLISH_STAGING),
                live_version=3,
            ),
            ServiceLifecycleCard(
                publication_id=3,
                version=3,
                display_state=DisplayState.SERVICE_OFFLINE,
                status="released",
                actions=(BotAction.VIEW,),
                live_version=3,
            ),
        )
    }
    inventory = BotInventoryService(
        bot_service=bot,
        desktop_service=desktop,
        business_space=NoopBusinessSpaceContext(),
        lifecycle_view=BotLifecycleView(lifecycle_port),
    )

    items, total = inventory.list_items(
        owner_id="u1",
        space=None,
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.CLOUD,
        page=1,
        page_size=10,
    )

    assert total == 3
    service_items = [item for item in items if item.bot_id == "s1"]
    assert [item.publication_id for item in service_items] == [4, 3]
    assert [item.card_id for item in service_items] == ["service:s1:4", "service:s1:3"]
    lifecycle_port.cards_for_bots.assert_called_once_with(bots=[service_row])
