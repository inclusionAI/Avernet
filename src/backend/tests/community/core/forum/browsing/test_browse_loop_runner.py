"""Runner-level tests for BbsBrowseLoopRunner — the single push seam.

The runner is constructed by hand with stub ``OpenApiBotPort`` and
``ForumServiceProtocol`` implementations; we assert the mode-match contract
plus the shape of the message pushed to the Bot, without booting the
injector or the scheduler.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.errors import NotFound, ValidationError
from agentclaw.community.core.forum.browsing.runner import BbsBrowseLoopRunner
from agentclaw.community.core.task.task_runner.client.ports import BotSendResult
from agentclaw.community.core.forum.models import BrowseSubscriptionRecord


def _sub(*, mode: str = "framework", bot_id: str = "bot-a") -> BrowseSubscriptionRecord:
    now = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    return BrowseSubscriptionRecord(
        bot_id=bot_id,
        owner_user_id="111111",
        mode=mode,
        note=None,
        created_at=now,
        updated_at=now,
    )


class _FakeBot:
    """Implements only ``send_message`` — the runner is fire-and-forget."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_message(
        self,
        *,
        bot_id: str,
        message: str,
        metadata: dict[str, Any],
    ) -> BotSendResult:
        self.sent.append(
            {"bot_id": bot_id, "message": message, "metadata": metadata}
        )
        return BotSendResult(run_id="run_x", session_id="sess_x")


class _FakeService:
    def __init__(self, sub: BrowseSubscriptionRecord | None) -> None:
        self._sub = sub

    def get_subscription(self, *, bot_id: str) -> BrowseSubscriptionRecord | None:
        return self._sub


@pytest.mark.asyncio
async def test_push_browse_once_returns_bot_result_and_injects_paths(monkeypatch):
    monkeypatch.setenv("BACKEND_URL", "https://bbs.test/api")
    runner = BbsBrowseLoopRunner(_FakeBot(), _FakeService(_sub(mode="framework", bot_id="bot-a")))
    result = await runner.push_browse_once(bot_id="bot-a", expected_mode="framework")
    bot = runner._bot  # type: ignore[attr-defined]

    assert result["run_id"] == "run_x"
    assert result["session_id"] == "sess_x"
    assert result["status"] == "submitted"
    assert result["mode"] == "framework"
    assert len(bot.sent) == 1
    sent = bot.sent[0]
    assert sent["bot_id"] == "bot-a"
    # message carries the [BBS-BROWSE] marker + the four resolved BBS paths.
    assert "[BBS-BROWSE]" in sent["message"]
    assert "/api/v1/bots/bot-a/bbs/feed" in sent["message"]
    assert "/api/v1/bbs/topics/{topic_id}" in sent["message"]
    assert "/api/v1/bots/bot-a/bbs/topics/{topic_id}/replies" in sent["message"]
    assert sent["metadata"] == {"biz_module": "bbs_browse_loop", "mode": "framework"}


@pytest.mark.asyncio
async def test_push_browse_once_mode_mismatch_raises_validation():
    runner = BbsBrowseLoopRunner(_FakeBot(), _FakeService(_sub(mode="openclaw")))
    with pytest.raises(ValidationError):
        await runner.push_browse_once(bot_id="bot-a", expected_mode="framework")


@pytest.mark.asyncio
async def test_push_browse_once_missing_subscription_raises_not_found():
    runner = BbsBrowseLoopRunner(_FakeBot(), _FakeService(None))
    with pytest.raises(NotFound):
        await runner.push_browse_once(bot_id="bot-a", expected_mode="framework")


@pytest.mark.asyncio
async def test_push_cron_event_only_for_openclaw(monkeypatch):
    runner = BbsBrowseLoopRunner(_FakeBot(), _FakeService(_sub(mode="framework")))
    # framework-mode subscription cannot receive cron events
    with pytest.raises(ValidationError):
        await runner.push_cron_event(bot_id="bot-a", action="register")

    runner = BbsBrowseLoopRunner(_FakeBot(), _FakeService(_sub(mode="openclaw", bot_id="bot-b")))
    result = await runner.push_cron_event(bot_id="bot-b", action="register")
    bot = runner._bot  # type: ignore[attr-defined]
    assert len(bot.sent) == 1
    assert result["run_id"] == "run_x"
    assert result["status"] == "submitted"
    assert result["mode"] == "openclaw"
    assert result.get("action") == "register"
    sent = bot.sent[0]
    assert "[BBS-BROWSE-CRON]" in sent["message"]
    assert "打开" in sent["message"] or "注册" in sent["message"]
    assert sent["metadata"]["action"] == "register"
    assert sent["metadata"]["mode"] == "openclaw"


@pytest.mark.asyncio
async def test_push_cron_event_remove_message(monkeypatch):
    runner = BbsBrowseLoopRunner(_FakeBot(), _FakeService(_sub(mode="openclaw", bot_id="bot-b")))
    result = await runner.push_cron_event(bot_id="bot-b", action="remove")
    sent = runner._bot.sent[0]  # type: ignore[attr-defined]
    assert "[BBS-BROWSE-CRON]" in sent["message"]
    assert "关闭" in sent["message"] or "删除" in sent["message"] or "移除" in sent["message"]
    assert result.get("action") == "remove"
    assert sent["metadata"]["action"] == "remove"
