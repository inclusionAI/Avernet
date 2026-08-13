"""Unit tests for BotInventoryService aggregation behavior."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_inventory.adapters.noop_business_space import NoopBusinessSpaceContext
from agentclaw.community.core.bot_inventory.adapters.noop_service_lifecycle import NoopServiceLifecyclePort
from agentclaw.community.core.bot_inventory.services.bot_inventory_service import (
    MAX_CLOUD_ROWS,
    BotInventoryService,
)
from agentclaw.community.core.bot_inventory.services.lifecycle_view import BotLifecycleView
from agentclaw.community.core.bot_inventory.types import DeployMode
from agentclaw.community.core.errors import NotFound


CLOUD = {
    "bot_id": "c1", "bot_name": "Cloud", "bot_desc": "cloud bot",
    "active_engine": "teclaw", "bot_type": "personal", "status": "ACTIVE", "owner_id": "u1",
}
LOCAL = {
    "bot_id": "l1", "bot_name": "Local", "bot_desc": "local bot",
    "active_engine": "openclaw", "bot_type": "desktop", "status": "OFFLINE", "owner_id": "u1",
}


@pytest.fixture
def service():
    bot = MagicMock()
    bot.list_bots_by_conditions.return_value = {"total": 1, "items": [CLOUD]}
    bot.get_bot.return_value = CLOUD
    desktop = MagicMock()
    desktop.list_user_bots.return_value = [LOCAL]
    return BotInventoryService(
        bot_service=bot,
        desktop_service=desktop,
        business_space=NoopBusinessSpaceContext(),
        lifecycle_view=BotLifecycleView(NoopServiceLifecyclePort()),
    ), bot, desktop


@pytest.mark.unit
def test_list_items_combines_filters_and_paginates(service) -> None:
    inventory, _, _ = service

    items, total = inventory.list_items(
        owner_id="u1", space=None, keyword=None, engine=None,
        deploy_mode=None, page=1, page_size=10,
    )

    assert total == 2
    assert {item.bot_id for item in items} == {"c1", "l1"}


@pytest.mark.unit
def test_cloud_source_is_capped_and_logs_truncation(service, caplog) -> None:
    inventory, bot, desktop = service
    cloud_rows = [
        {**CLOUD, "bot_id": f"c{i:04d}", "bot_name": f"Cloud {i:04d}"}
        for i in range(MAX_CLOUD_ROWS + 200)
    ]

    def list_page(**kwargs):
        page = kwargs["page"]
        page_size = kwargs["page_size"]
        start = (page - 1) * page_size
        return {"total": len(cloud_rows), "items": cloud_rows[start : start + page_size]}

    bot.list_bots_by_conditions.side_effect = list_page
    desktop.list_user_bots.return_value = []

    items, total = inventory.list_items(
        owner_id="u1", space=None, keyword=None, engine=None,
        deploy_mode=DeployMode.CLOUD, page=1, page_size=MAX_CLOUD_ROWS + 500,
    )

    assert total == MAX_CLOUD_ROWS
    assert len(items) == MAX_CLOUD_ROWS
    assert bot.list_bots_by_conditions.call_count == MAX_CLOUD_ROWS // 200
    assert "truncated cloud rows" in caplog.text
