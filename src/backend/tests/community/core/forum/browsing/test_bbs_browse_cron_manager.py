"""Unit tests for BbsBrowseCronManager — fixed-name upsert + legacy collapse."""

from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.forum.browsing.cron_setup import (
    BBS_BROWSE_LOOP_CRON_EXPR,
    BBS_BROWSE_LOOP_CRON_KIND,
    BBS_BROWSE_LOOP_CRON_NAME,
    BBS_BROWSE_LOOP_CRON_TIMEOUT_SECS,
    BBS_BROWSE_RUN_TRIGGER,
    BbsBrowseCronManager,
    bbs_browse_loop_cron_body,
)


class _FakeCronRelay:
    """Records list/create/update/delete with a mutable in-memory task store."""

    def __init__(self, tasks: list[dict[str, Any]] | None = None) -> None:
        self._tasks: dict[str, dict[str, Any]] = {
            t["id"]: dict(t) for t in (tasks or [])
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_all_crons(self, *, user_id, nick_name, bot_id, runtime_stage=None):
        self.calls.append(("list", {"bot_id": bot_id, "user_id": user_id, "nick_name": nick_name}))
        return {"success": True, "data": list(self._tasks.values())}

    async def create_cron(self, *, bot_id, user_id, nick_name, body):
        self.calls.append(("create", {"bot_id": bot_id, "user_id": user_id, "body": body}))
        tid = f"task-{len(self._tasks) + 1}"
        self._tasks[tid] = {"id": tid, **body}
        return {"success": True, "data": self._tasks[tid]}

    async def update_cron(self, *, bot_id, user_id, nick_name, task_id, body, runtime_stage=None):
        self.calls.append(("update", {"bot_id": bot_id, "task_id": task_id, "body": body}))
        self._tasks[task_id].update(body)
        return {"success": True, "data": self._tasks[task_id]}

    async def delete_cron(self, *, bot_id, user_id, nick_name, task_id, runtime_stage=None):
        self.calls.append(("delete", {"bot_id": bot_id, "task_id": task_id}))
        self._tasks.pop(task_id, None)
        return {"success": True}

    def task_names(self) -> list[str]:
        return [t.get("name") for t in self._tasks.values()]


def _legacy_tasks() -> list[dict[str, Any]]:
    # The two redundant crons observed on the pre bot before this fix.
    return [
        {"id": "t-feed", "name": "bbs-browse-feed-reader", "schedule": "*/30 * * * *"},
        {"id": "t-30min", "name": "bbs-browse-30min", "schedule": "*/30 * * * *"},
    ]


def test_cron_body_is_fixed_deterministic():
    body = bbs_browse_loop_cron_body()
    assert body == {
        "name": BBS_BROWSE_LOOP_CRON_NAME,
        "schedule": BBS_BROWSE_LOOP_CRON_EXPR,
        "command": BBS_BROWSE_RUN_TRIGGER,
        "timezone": "Asia/Shanghai",
        "enabled": True,
        "timeout_secs": BBS_BROWSE_LOOP_CRON_TIMEOUT_SECS,
        "kind": BBS_BROWSE_LOOP_CRON_KIND,
    }
    # trigger carries no urls (skill owns them) and the [BBS-BROWSE] marker
    assert "http" not in BBS_BROWSE_RUN_TRIGGER
    assert "/api/" not in BBS_BROWSE_RUN_TRIGGER
    assert BBS_BROWSE_RUN_TRIGGER.startswith("[BBS-BROWSE]")


@pytest.mark.asyncio
async def test_ensure_cron_creates_canonical_when_absent():
    relay = _FakeCronRelay(tasks=[])
    mgr = BbsBrowseCronManager(cron_relay=relay)
    await mgr.ensure_cron(bot_id="bot-a", owner_user_id="149844")
    creates = [c for op, c in relay.calls if op == "create"]
    assert len(creates) == 1
    assert creates[0]["body"] == bbs_browse_loop_cron_body()
    assert relay.task_names() == [BBS_BROWSE_LOOP_CRON_NAME]


@pytest.mark.asyncio
async def test_ensure_cron_collapses_legacy_into_single_canonical():
    relay = _FakeCronRelay(tasks=_legacy_tasks())
    mgr = BbsBrowseCronManager(cron_relay=relay)
    await mgr.ensure_cron(bot_id="bot-a", owner_user_id="149844")
    # both legacy deleted, one canonical created
    deletes = [c for op, c in relay.calls if op == "delete"]
    creates = [c for op, c in relay.calls if op == "create"]
    assert {d["task_id"] for d in deletes} == {"t-feed", "t-30min"}
    assert len(creates) == 1
    assert creates[0]["body"]["name"] == BBS_BROWSE_LOOP_CRON_NAME
    assert relay.task_names() == [BBS_BROWSE_LOOP_CRON_NAME]


@pytest.mark.asyncio
async def test_ensure_cron_updates_canonical_when_present_no_duplicate():
    relay = _FakeCronRelay(
        tasks=[{"id": "t-can", "name": BBS_BROWSE_LOOP_CRON_NAME, "schedule": "old"}]
    )
    mgr = BbsBrowseCronManager(cron_relay=relay)
    await mgr.ensure_cron(bot_id="bot-a", owner_user_id="149844")
    updates = [c for op, c in relay.calls if op == "update"]
    creates = [c for op, c in relay.calls if op == "create"]
    assert len(updates) == 1 and updates[0]["task_id"] == "t-can"
    assert updates[0]["body"] == bbs_browse_loop_cron_body()
    assert creates == []
    assert relay.task_names() == [BBS_BROWSE_LOOP_CRON_NAME]


@pytest.mark.asyncio
async def test_ensure_cron_collapses_legacy_alongside_canonical():
    relay = _FakeCronRelay(
        tasks=[
            {"id": "t-can", "name": BBS_BROWSE_LOOP_CRON_NAME, "schedule": "old"},
            {"id": "t-legacy", "name": "bbs-browse-feed-reader", "schedule": "*/30 * * * *"},
        ]
    )
    mgr = BbsBrowseCronManager(cron_relay=relay)
    await mgr.ensure_cron(bot_id="bot-a", owner_user_id="149844")
    deletes = [c for op, c in relay.calls if op == "delete"]
    updates = [c for op, c in relay.calls if op == "update"]
    assert {d["task_id"] for d in deletes} == {"t-legacy"}
    assert len(updates) == 1
    assert relay.task_names() == [BBS_BROWSE_LOOP_CRON_NAME]


@pytest.mark.asyncio
async def test_ensure_cron_does_not_touch_unrelated_crons():
    relay = _FakeCronRelay(
        tasks=[
            {"id": "t-other", "name": "okr-weekly", "schedule": "0 9 * * 1"},
            {"id": "t-legacy", "name": "bbs-browse-30min", "schedule": "*/30 * * * *"},
        ]
    )
    mgr = BbsBrowseCronManager(cron_relay=relay)
    await mgr.ensure_cron(bot_id="bot-a", owner_user_id="149844")
    deletes = [c for op, c in relay.calls if op == "delete"]
    assert {d["task_id"] for d in deletes} == {"t-legacy"}
    # unrelated cron survives
    assert "okr-weekly" in relay.task_names()
    assert relay.task_names() == ["okr-weekly", BBS_BROWSE_LOOP_CRON_NAME]


@pytest.mark.asyncio
async def test_remove_cron_deletes_canonical_and_legacy_idempotent():
    relay = _FakeCronRelay(
        tasks=[
            {"id": "t-can", "name": BBS_BROWSE_LOOP_CRON_NAME, "schedule": "*/30 * * * *"},
            {"id": "t-legacy", "name": "bbs-browse-feed-reader", "schedule": "*/30 * * * *"},
            {"id": "t-other", "name": "okr-weekly", "schedule": "0 9 * * 1"},
        ]
    )
    mgr = BbsBrowseCronManager(cron_relay=relay)
    await mgr.remove_cron(bot_id="bot-a", owner_user_id="149844")
    assert relay.task_names() == ["okr-weekly"]
    # idempotent: remove again is a no-op
    await mgr.remove_cron(bot_id="bot-a", owner_user_id="149844")
    assert relay.task_names() == ["okr-weekly"]
