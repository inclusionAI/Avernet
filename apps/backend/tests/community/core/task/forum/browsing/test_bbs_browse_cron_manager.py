"""Unit tests for BbsBrowseCronManager — fixed-name upsert + legacy collapse."""

from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.task.forum.browsing.cron_setup import (
    BBS_BROWSE_LOOP_CRON_EXPR,
    BBS_BROWSE_LOOP_CRON_KIND,
    BBS_BROWSE_LOOP_CRON_NAME,
    BBS_BROWSE_LOOP_CRON_TIMEOUT_SECS,
    BBS_BROWSE_RUN_TRIGGER,
    BbsBrowseCronManager,
    BbsDigestCronManager,
    bbs_browse_loop_cron_body,
    bbs_digest_loop_cron_body,
    bbs_digest_loop_cron_expr,
    BBS_DIGEST_LOOP_CRON_NAME,
    BBS_DIGEST_LOOP_CRON_TIMEOUT_SECS,
    BBS_DIGEST_RUN_TRIGGER,
    FORUM_DIGEST_RUN_TRIGGER,
)
from agentclaw.community.core.task.forum.browsing.messages import (
    browse_cron_message,
    browse_skill_name,
    digest_cron_message,
)


_BACKEND_URL = "http://127.0.0.1:8888"
expected_bbs_cron_command = browse_cron_message(
    bot_id="bot-a", backend_base_url=_BACKEND_URL
)


class _FakeCronRelay:
    """Records list/create/update/delete with a mutable in-memory task store."""

    def __init__(
        self,
        tasks: list[dict[str, Any]] | None = None,
        *,
        list_result: dict[str, Any] | None = None,
        create_result: dict[str, Any] | None = None,
    ) -> None:
        self._tasks: dict[str, dict[str, Any]] = {
            t["id"]: dict(t) for t in (tasks or [])
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.list_result: dict[str, Any] | None = list_result
        self.create_result: dict[str, Any] | None = create_result

    async def list_all_crons(self, *, user_id, nick_name, bot_id, runtime_stage=None):
        self.calls.append(
            ("list", {"bot_id": bot_id, "user_id": user_id, "nick_name": nick_name})
        )
        if self.list_result is not None:
            return self.list_result
        return {"success": True, "data": list(self._tasks.values())}

    async def create_cron(self, *, bot_id, user_id, nick_name, body):
        self.calls.append(
            ("create", {"bot_id": bot_id, "user_id": user_id, "body": body})
        )
        if self.create_result is not None:
            return self.create_result
        tid = f"task-{len(self._tasks) + 1}"
        self._tasks[tid] = {"id": tid, **body}
        return {"success": True, "data": self._tasks[tid]}

    async def update_cron(
        self, *, bot_id, user_id, nick_name, task_id, body, runtime_stage=None
    ):
        self.calls.append(
            ("update", {"bot_id": bot_id, "task_id": task_id, "body": body})
        )
        self._tasks[task_id].update(body)
        return {"success": True, "data": self._tasks[task_id]}

    async def delete_cron(
        self, *, bot_id, user_id, nick_name, task_id, runtime_stage=None
    ):
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


def test_openclaw_cron_cadence_is_hourly():
    assert BBS_BROWSE_LOOP_CRON_EXPR == "0 * * * *"


def test_cron_body_is_fixed_deterministic():
    body = bbs_browse_loop_cron_body(bot_id="bot-a", backend_base_url=_BACKEND_URL)
    assert body == {
        "name": BBS_BROWSE_LOOP_CRON_NAME,
        "schedule": BBS_BROWSE_LOOP_CRON_EXPR,
        "command": expected_bbs_cron_command,
        "timezone": "Asia/Shanghai",
        "enabled": True,
        "timeout_secs": BBS_BROWSE_LOOP_CRON_TIMEOUT_SECS,
        "kind": BBS_BROWSE_LOOP_CRON_KIND,
    }


def test_cron_trigger_carries_trusted_backend_not_a_bcs_url():
    command = bbs_browse_loop_cron_body(bot_id="bot-a", backend_base_url=_BACKEND_URL)[
        "command"
    ]
    assert BBS_BROWSE_RUN_TRIGGER.startswith("[BBS-BROWSE]")
    assert f'"backend": "{_BACKEND_URL}"' in command
    assert '"bot_id": "bot-a"' in command
    assert '"subscription_mode": "openclaw"' in command
    assert "/api/v1/collaboration/tasks/bbs/topics" not in command
    assert "21000" not in command


def test_cron_command_uses_environment_selected_skill(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "dev")
    dev_command = bbs_browse_loop_cron_body(
        bot_id="bot-a", backend_base_url=_BACKEND_URL
    )["command"]
    assert f"使用当前 Bot 已激活的 {browse_skill_name('dev')} Skill" in dev_command

    monkeypatch.setenv("SERVER_ENV", "prod")
    prod_command = bbs_browse_loop_cron_body(
        bot_id="bot-a", backend_base_url=_BACKEND_URL
    )["command"]
    assert f"使用当前 Bot 已激活的 {browse_skill_name('prod')} Skill" in prod_command


@pytest.mark.asyncio
async def test_ensure_cron_creates_canonical_when_absent():
    relay = _FakeCronRelay(tasks=[])
    mgr = BbsBrowseCronManager(cron_relay=relay, backend_base_url=_BACKEND_URL)
    await mgr.ensure_cron(bot_id="bot-a", owner_user_id="149844")
    creates = [c for op, c in relay.calls if op == "create"]
    assert len(creates) == 1
    assert creates[0]["body"] == bbs_browse_loop_cron_body(
        bot_id="bot-a", backend_base_url=_BACKEND_URL
    )
    assert relay.task_names() == [BBS_BROWSE_LOOP_CRON_NAME]


@pytest.mark.asyncio
async def test_ensure_cron_collapses_legacy_into_single_canonical():
    relay = _FakeCronRelay(tasks=_legacy_tasks())
    mgr = BbsBrowseCronManager(cron_relay=relay, backend_base_url=_BACKEND_URL)
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
    mgr = BbsBrowseCronManager(cron_relay=relay, backend_base_url=_BACKEND_URL)
    await mgr.ensure_cron(bot_id="bot-a", owner_user_id="149844")
    updates = [c for op, c in relay.calls if op == "update"]
    creates = [c for op, c in relay.calls if op == "create"]
    assert len(updates) == 1 and updates[0]["task_id"] == "t-can"
    assert updates[0]["body"] == bbs_browse_loop_cron_body(
        bot_id="bot-a", backend_base_url=_BACKEND_URL
    )
    assert creates == []
    assert relay.task_names() == [BBS_BROWSE_LOOP_CRON_NAME]


@pytest.mark.asyncio
async def test_ensure_cron_collapses_legacy_alongside_canonical():
    relay = _FakeCronRelay(
        tasks=[
            {"id": "t-can", "name": BBS_BROWSE_LOOP_CRON_NAME, "schedule": "old"},
            {
                "id": "t-legacy",
                "name": "bbs-browse-feed-reader",
                "schedule": "*/30 * * * *",
            },
        ]
    )
    mgr = BbsBrowseCronManager(cron_relay=relay, backend_base_url=_BACKEND_URL)
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
    mgr = BbsBrowseCronManager(cron_relay=relay, backend_base_url=_BACKEND_URL)
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
            {
                "id": "t-can",
                "name": BBS_BROWSE_LOOP_CRON_NAME,
                "schedule": "*/30 * * * *",
            },
            {
                "id": "t-legacy",
                "name": "bbs-browse-feed-reader",
                "schedule": "*/30 * * * *",
            },
            {"id": "t-other", "name": "okr-weekly", "schedule": "0 9 * * 1"},
        ]
    )
    mgr = BbsBrowseCronManager(cron_relay=relay, backend_base_url=_BACKEND_URL)
    await mgr.remove_cron(bot_id="bot-a", owner_user_id="149844")
    assert relay.task_names() == ["okr-weekly"]
    # idempotent: remove again is a no-op
    await mgr.remove_cron(bot_id="bot-a", owner_user_id="149844")
    assert relay.task_names() == ["okr-weekly"]


@pytest.mark.asyncio
async def test_ensure_cron_fails_when_draft_list_has_failed_targets():
    relay = _FakeCronRelay(
        list_result={
            "success": True,
            "data": [],
            "failed_targets": [{"reason": "timeout"}],
        },
    )
    mgr = BbsBrowseCronManager(cron_relay=relay, backend_base_url=_BACKEND_URL)
    with pytest.raises(RuntimeError, match="failed targets"):
        await mgr.ensure_cron(bot_id="bot-a", owner_user_id="149844")
    assert all(op != "create" for op, _ in relay.calls)


@pytest.mark.asyncio
async def test_ensure_cron_fails_when_create_is_not_acknowledged():
    relay = _FakeCronRelay(
        create_result={"success": False, "message": "runtime unavailable"}
    )
    mgr = BbsBrowseCronManager(cron_relay=relay, backend_base_url=_BACKEND_URL)
    with pytest.raises(RuntimeError, match="did not acknowledge cron setup"):
        await mgr.ensure_cron(bot_id="bot-a", owner_user_id="149844")
    assert relay.task_names() == []


def test_digest_cron_cadence_is_environment_selected():
    assert bbs_digest_loop_cron_expr("dev") == "0 * * * *"
    assert bbs_digest_loop_cron_expr("pre") == "0 * * * *"
    assert bbs_digest_loop_cron_expr("prod") == "0 20 * * *"


def test_digest_cron_body_is_fixed_and_environment_selected(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "dev")
    dev_body = bbs_digest_loop_cron_body(bot_id="bot-a", backend_base_url=_BACKEND_URL)
    assert dev_body == {
        "name": BBS_DIGEST_LOOP_CRON_NAME,
        "schedule": "0 * * * *",
        "command": digest_cron_message(bot_id="bot-a", backend_base_url=_BACKEND_URL),
        "timezone": "Asia/Shanghai",
        "enabled": True,
        "timeout_secs": BBS_DIGEST_LOOP_CRON_TIMEOUT_SECS,
        "kind": BBS_BROWSE_LOOP_CRON_KIND,
    }
    assert dev_body["command"] == (
        "[FORUM-DIGEST-COLLECT]\n"
        "使用 $forum-digest-handler 执行一次 Forum digest collect。"
    )
    assert FORUM_DIGEST_RUN_TRIGGER == "[FORUM-DIGEST-COLLECT]"

    monkeypatch.setenv("SERVER_ENV", "prod")
    prod_body = bbs_digest_loop_cron_body(bot_id="bot-a", backend_base_url=_BACKEND_URL)
    assert prod_body["schedule"] == "0 20 * * *"
    assert BBS_DIGEST_RUN_TRIGGER == "[BBS-DIGEST-COLLECT]"
    assert prod_body["command"] == (
        "[BBS-DIGEST-COLLECT]\n使用 $bbs-digest-handler 执行一次 BBS digest collect。"
    )


@pytest.mark.asyncio
async def test_ensure_digest_cron_creates_canonical_when_absent(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "dev")
    relay = _FakeCronRelay(tasks=[])
    mgr = BbsDigestCronManager(cron_relay=relay, backend_base_url=_BACKEND_URL)
    await mgr.ensure_cron(bot_id="bot-a", owner_user_id="149844")

    creates = [c for op, c in relay.calls if op == "create"]
    assert len(creates) == 1
    assert creates[0]["body"] == bbs_digest_loop_cron_body(
        bot_id="bot-a", backend_base_url=_BACKEND_URL
    )
    assert relay.task_names() == [BBS_DIGEST_LOOP_CRON_NAME]


@pytest.mark.asyncio
async def test_ensure_digest_cron_collapse_legacy_without_touching_browse(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "pre")
    relay = _FakeCronRelay(
        tasks=[
            {"id": "t-browse", "name": BBS_BROWSE_LOOP_CRON_NAME, "schedule": "old"},
            {"id": "t-legacy", "name": "bbs-digest-handler", "schedule": "old"},
            {"id": "t-other", "name": "okr-weekly", "schedule": "old"},
        ]
    )
    mgr = BbsDigestCronManager(cron_relay=relay, backend_base_url=_BACKEND_URL)
    await mgr.ensure_cron(bot_id="bot-a", owner_user_id="149844")

    deletes = [c for op, c in relay.calls if op == "delete"]
    assert {d["task_id"] for d in deletes} == {"t-legacy"}
    assert relay.task_names() == [
        BBS_BROWSE_LOOP_CRON_NAME,
        "okr-weekly",
        BBS_DIGEST_LOOP_CRON_NAME,
    ]


@pytest.mark.asyncio
async def test_remove_digest_cron_is_idempotent_and_namespace_scoped():
    relay = _FakeCronRelay(
        tasks=[
            {"id": "t-digest", "name": BBS_DIGEST_LOOP_CRON_NAME, "schedule": "x"},
            {"id": "t-legacy", "name": "bbs-digest-handler", "schedule": "x"},
            {"id": "t-browse", "name": BBS_BROWSE_LOOP_CRON_NAME, "schedule": "x"},
            {"id": "t-other", "name": "okr-weekly", "schedule": "x"},
        ]
    )
    mgr = BbsDigestCronManager(cron_relay=relay, backend_base_url=_BACKEND_URL)
    await mgr.remove_cron(bot_id="bot-a", owner_user_id="149844")
    assert relay.task_names() == [BBS_BROWSE_LOOP_CRON_NAME, "okr-weekly"]
    await mgr.remove_cron(bot_id="bot-a", owner_user_id="149844")
    assert relay.task_names() == [BBS_BROWSE_LOOP_CRON_NAME, "okr-weekly"]
