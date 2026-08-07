"""Tests for collaborator-protected Harness endpoints.

Both cases drive the real router over the real repository, so what is
asserted is the persisted outcome rather than a call count on a patched
method. ``POST /api/harness/diagnose`` spawns its scan with
``asyncio.create_task``; the async client shares the test's event loop, so
:func:`drain_background_tasks` runs that scan to completion instead of the
scan being suppressed — which is also what lets the second case assert the
status the scan actually wrote back.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.harness.repository_protocol import (
    HarnessScanRecordRepository,
)
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import drain_background_tasks


def _scan_records(world, *, bot_id: str, entity_id: str) -> list[dict]:
    """Every scan record the repository holds for this bot."""
    records, _total = world.get(HarnessScanRecordRepository).list_records(
        bot_id=bot_id, entity_id=entity_id,
    )
    return records


@pytest.mark.asyncio
async def test_diagnose_rejects_nonexistent_bot_without_creating_scan(
    async_client, world,
):
    make_staff_user(world, user_id="u_owner")

    response = await async_client.post(
        "/api/harness/diagnose",
        headers={"x-user-id": "u_owner"},
        json={
            "bot_id": "nonexistent_bot",
            "entity_id": "u_owner",
            "entity_type": "staff",
        },
    )
    await drain_background_tasks()

    assert response.status_code == 404
    assert response.json()["detail"] == "Bot not found: nonexistent_bot"
    # The bot lookup precedes both the record insert and the scan task, so a
    # rejected request must leave nothing behind.
    assert _scan_records(world, bot_id="nonexistent_bot", entity_id="u_owner") == []


@pytest.mark.asyncio
async def test_diagnose_starts_scan_for_existing_bot(async_client, world):
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner")

    response = await async_client.post(
        "/api/harness/diagnose",
        headers={"x-user-id": "u_owner"},
        json={
            "bot_id": "bot_test",
            "entity_id": "u_owner",
            "entity_type": "staff",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "scanning"
    scan_id = body["scan_id"]
    assert scan_id > 0

    # The handler persists the record before spawning the task, which is what
    # makes the returned scan_id pollable straight away.
    repo = world.get(HarnessScanRecordRepository)
    assert repo.get_by_id(scan_id) is not None
    assert [r["id"] for r in _scan_records(
        world, bot_id="bot_test", entity_id="u_owner",
    )] == [scan_id]

    # Run the spawned scan to completion. This bot has no synced config files,
    # so the scan's own empty-input guard is what ends it — and it records that
    # verdict against the same scan_id the response handed out.
    await drain_background_tasks()
    assert repo.get_by_id(scan_id)["status"] == "failed"
