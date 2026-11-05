"""Bulk Apply preserves ordinary Set display snapshots in its real SQLite UoW."""

import pytest
from sqlalchemy import event

from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
)
from tests.community.repository.skill_center.capability_desired_state_support import (
    _Database,
)
from ._test_skill_set_management_service_support import _Runtime, _skill_service


@pytest.mark.asyncio
async def test_bulk_new_mcp_uses_catalog_snapshot_and_keeps_existing_snapshot():
    db = _Database()
    service = _skill_service(CapabilityDesiredStateRepository(db), _Runtime())
    catalog = {
        "mcp.first": {
            "name": "Calendar assistant",
            "description": "First description",
            "icon": "first-icon",
        },
        "mcp.second": {
            "name": "Search assistant",
            "description": "Second description",
            "icon": "second-icon",
        },
        "mcp.failed": {
            "name": "Rolled back",
            "description": "Never committed",
            "icon": "failed-icon",
        },
    }
    reads = []

    def detail(code):
        reads.append(code)
        return {"runMode": "LOCAL", **catalog[code]}

    service._mcp_center.get_mcp_detail = detail
    before = service.read_apply_snapshot(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner"
    )

    async def replace(name, codes):
        return await service.replace_apply_skill_sets(
            scope=before.scope,
            actor_id="true-owner",
            sets=(ResolvedSkillSetWrite(name, is_active=False, mcp_codes=codes),),
            remove_names=(),
            default_change=DefaultExclusionChange(),
            platform_default_codes=before.platform_default_codes,
            project=False,
        )

    def displays():
        groups = service.list_resources(
            bot_id="bot-1", owner_id="true-owner", user_id="true-owner"
        )
        return {
            group["name"]: {
                member["server_code"]: (
                    member["name"],
                    member["description"],
                    member["icon"],
                )
                for member in group["mcps"]
            }
            for group in groups
        }

    await replace("expert", ("mcp.first",))
    assert displays() == {
        "expert": {
            "mcp.first": ("Calendar assistant", "First description", "first-icon")
        }
    }
    catalog["mcp.first"] = {
        "name": "Renamed upstream",
        "description": "Changed upstream",
        "icon": "changed-icon",
    }
    repeated = await replace("expert", ("mcp.first",))
    assert repeated.unchanged == ("expert",)
    await replace("expert", ("mcp.first", "mcp.second"))
    expected = {
        "expert": {
            "mcp.first": ("Calendar assistant", "First description", "first-icon"),
            "mcp.second": ("Search assistant", "Second description", "second-icon"),
        }
    }
    assert displays() == expected

    def fail_member_write(session, _context, _instances):
        if any(
            isinstance(row, SkillSetMCPServer) and row.server_code == "mcp.failed"
            for row in session.new
        ):
            raise RuntimeError("member persistence failed")

    event.listen(db.sessions.class_, "before_flush", fail_member_write)
    try:
        with pytest.raises(RuntimeError, match="member persistence failed"):
            await replace("draft", ("mcp.failed",))
    finally:
        event.remove(db.sessions.class_, "before_flush", fail_member_write)
    assert displays() == expected
    assert reads == ["mcp.first", "mcp.first", "mcp.first", "mcp.second", "mcp.failed"]
