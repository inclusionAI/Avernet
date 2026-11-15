"""Repair only collaboration metadata, never caller identity configuration."""

import pytest

from agentclaw.community.kernel.collaboration_repair import COMPONENT_ORDER
from tests.community.core.bot_management.collaboration_repair.factories import (
    make_repair_case,
)


@pytest.mark.asyncio
async def test_repair_returns_only_three_components_and_never_sends_caller():
    case = make_repair_case()

    report = await case.service.repair(case.command)

    assert report.status == "succeeded"
    assert tuple(item.component for item in report.items) == (
        "ownership",
        "profile",
        "managers",
    )
    assert not any(call[0] == "metadata.sync_caller" for call in case.calls.mock_calls)


def test_component_contract_excludes_caller_configuration():
    assert COMPONENT_ORDER == ("ownership", "profile", "managers")


def test_http_schema_requires_three_items_and_excludes_caller():
    from pydantic import ValidationError

    from agentclaw.community.adapters.http.openapi_v1.schemas_collaboration_repair import (
        CollaborationRepairReport,
    )

    items = [
        {"component": component, "status": "succeeded"} for component in COMPONENT_ORDER
    ]
    report = CollaborationRepairReport(bot_id="b", status="succeeded", items=items)
    assert len(report.items) == 3
    for invalid in (
        items[:2],
        [*items, {"component": "caller_config", "status": "succeeded"}],
        [*items[:2], {"component": "caller_config", "status": "succeeded"}],
    ):
        with pytest.raises(ValidationError):
            CollaborationRepairReport(bot_id="b", status="succeeded", items=invalid)
