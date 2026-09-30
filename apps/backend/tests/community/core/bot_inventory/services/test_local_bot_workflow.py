"""Unit tests for local Bot workflow filtering."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_inventory.adapters.noop_business_space import (
    NoopBusinessSpaceContext,
)
from agentclaw.community.core.bot_inventory.services.local_bot_workflow import (
    LocalBotWorkflowService,
)


@pytest.mark.unit
def test_grant_filter_precedes_local_pagination_and_total() -> None:
    desktop = MagicMock()
    desktop.list_user_bots.return_value = [
        {
            "bot_id": f"l{i}",
            "bot_name": f"Local {i}",
            "active_engine": "openclaw",
        }
        for i in range(1, 5)
    ]
    workflow = LocalBotWorkflowService(
        desktop_service=desktop,
        business_space=NoopBusinessSpaceContext(),
        passport_plugin=MagicMock(),
        auth_relationship_plugin=MagicMock(),
    )

    total, rows = workflow.list_bots(
        owner_id="u1",
        header_space_id=None,
        keyword=None,
        engine=None,
        bot_ids=["l1", "l3", "l4"],
        page=2,
        page_size=2,
    )

    assert total == 3
    assert [row["bot_id"] for row in rows] == ["l4"]
