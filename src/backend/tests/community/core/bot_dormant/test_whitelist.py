"""Integration tests for WhitelistService.batch_add.

Uses world.get(DatabasePlugin) to obtain the per-test SQLite DatabasePlugin,
consistent with the framework fixture idiom (same injector the app uses).

Test scenario:
  - Insert 3 entries: 2 unique bot_ids + 1 duplicate.
  - Expect: inserted=2, skipped=1 (duplicate silently skipped).
"""
import pytest

from agentclaw.community.core.bot_dormant.whitelist_service import WhitelistService
from agentclaw.community.plugin_api.database import DatabasePlugin


@pytest.mark.integration
def test_batch_add_dedup(world):
    """重复 bot_id 跳过，不报错。inserted=2, skipped=1."""
    db = world.get(DatabasePlugin)
    svc = WhitelistService(db)
    entries = [
        {"bot_id": "b1", "owner_id": "u1"},
        {"bot_id": "b1", "owner_id": "u1"},  # duplicate — should be skipped
        {"bot_id": "b2", "owner_id": "u2"},
    ]
    result = svc.batch_add(entries, created_by="ops")
    assert result == {"inserted": 2, "skipped": 1}
