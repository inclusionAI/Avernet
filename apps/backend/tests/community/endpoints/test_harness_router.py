"""Tests for collaborator-protected Harness endpoints.

These cases drive the real router over the real repository, so what is
asserted is the persisted outcome rather than a call count on a patched
method. ``POST /api/harness/diagnose`` spawns its scan with
``asyncio.create_task``; the async client shares the test's event loop, so
:func:`drain_background_tasks` runs that scan to completion instead of the
scan being suppressed — which is also what lets the second case assert the
status the scan actually wrote back.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.repository.protocols.harness import HarnessScanRecordRepository
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.factories.devices import make_active_arca_device
from tests.community.framework import drain_background_tasks
from tests.community.framework.device_seams import route_device_accessor_to_local


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
@pytest.mark.parametrize(
    ("device_bound", "publish_id", "failed_reason"),
    [
        pytest.param(
            True, None, "请先给 Bot 发一条消息完成初始化，再回来运行健康检测。",
            id="empty-draft",
        ),
        pytest.param(
            False, None, "Bot 配置文件读取失败。请确认 Bot 可访问后重试。",
            id="draft-read-failed",
        ),
        pytest.param(
            True, "999999", "未读取到健康检测所需的 Bot 配置内容。请检查配置后重试。",
            id="unavailable-published-target",
        ),
    ],
)
async def test_diagnose_starts_scan_for_existing_bot(
    async_client, world, device_bound, publish_id, failed_reason,
):
    make_staff_user(world, user_id="u_owner")
    binding_id = None
    if device_bound:
        binding_id = make_active_arca_device(
            world, owner_id="u_owner", device_id="arca_test_device",
        )
        route_device_accessor_to_local(world)
    make_bot(
        world, bot_id="bot_test", owner_id="u_owner", active_engine="openclaw",
        binding_id=binding_id,
    )

    response = await async_client.post(
        "/api/harness/diagnose",
        headers={"x-user-id": "u_owner"},
        json={
            "bot_id": "bot_test",
            "entity_id": "u_owner",
            "entity_type": "staff",
            "bot_publish_id": publish_id,
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

    await drain_background_tasks()
    record = repo.get_by_id(scan_id)
    assert record["status"] == "failed"
    assert record["failed_reason"] == failed_reason
    assert record["check_items"]
    assert {item["result_detail"] for item in record["check_items"]} == {
        "未读到配置内容" if device_bound else "配置文件读取失败"
    }

    report = await async_client.get(
        "/api/harness/diagnose/dim-report",
        headers={"x-user-id": "u_owner"},
        params={"bot_id": "bot_test", "entity_id": "u_owner"},
    )
    assert report.status_code == 200
    [item] = report.json()["items"]
    assert item["failed_reason"] == failed_reason
