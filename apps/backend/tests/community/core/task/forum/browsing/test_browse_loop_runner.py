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
from agentclaw.community.core.task.forum.browsing.runner import BbsBrowseLoopRunner
from agentclaw.community.core.task.forum.browsing.messages import (
    bbs_browse_skill_names,
    browse_skill_name,
    digest_cron_message,
)
from agentclaw.community.core.task.task_runner.client.ports import BotSendResult
from agentclaw.community.core.task.forum.models import (
    BBS_BROWSE_LOOP_CRON_NAME,
    BrowseSubscriptionRecord,
)


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
        self.sent.append({"bot_id": bot_id, "message": message, "metadata": metadata})
        return BotSendResult(run_id="run_x", session_id="sess_x")


class _FakeService:
    def __init__(self, sub: BrowseSubscriptionRecord | None) -> None:
        self._sub = sub

    def get_subscription(self, *, bot_id: str) -> BrowseSubscriptionRecord | None:
        return self._sub


def _runner(bot, service) -> BbsBrowseLoopRunner:
    return BbsBrowseLoopRunner(
        bot,
        service,
        backend_url="https://bbs.test",
    )


def test_browse_skill_name_is_environment_selected():
    assert browse_skill_name("prod") == "bbs-browse"
    assert browse_skill_name("pre") == "forum-browse"
    assert browse_skill_name("dev") == "forum-browse"
    assert bbs_browse_skill_names("prod") == (
        "bbs-browse",
        "bbs-create-topic",
        "bbs-topic-handler",
        "bbs-digest-handler",
    )
    assert bbs_browse_skill_names("pre") == (
        "forum-browse",
        "forum-create-topic",
        "forum-topic-handler",
        "forum-digest-handler",
    )


@pytest.mark.asyncio
async def test_push_browse_once_returns_bot_result_and_injects_runtime_context(
    monkeypatch,
):
    monkeypatch.setenv("SERVER_ENV", "dev")
    runner = _runner(_FakeBot(), _FakeService(_sub(mode="framework", bot_id="bot-a")))
    result = await runner.push_browse_once(bot_id="bot-a", expected_mode="framework")
    bot = runner._bot  # type: ignore[attr-defined]

    assert result["run_id"] == "run_x"
    assert result["session_id"] == "sess_x"
    assert result["status"] == "submitted"
    assert result["mode"] == "framework"
    assert len(bot.sent) == 1
    sent = bot.sent[0]
    assert sent["bot_id"] == "bot-a"
    assert "[BBS-BROWSE]" in sent["message"]
    assert "执行一次 forum-browse 逛论坛流程" in sent["message"]
    assert "使用当前 Bot 已激活的 forum-browse Skill" in sent["message"]
    # The runner only injects trusted identity and origin; the activated skill
    # owns the BBS API paths.
    assert '"bot_id": "bot-a"' in sent["message"]
    assert '"backend": "https://bbs.test"' in sent["message"]
    assert '"subscription_mode": "framework"' in sent["message"]
    assert "/api/v1/collaboration/tasks/bbs/" not in sent["message"]
    assert sent["metadata"] == {"biz_module": "bbs_browse_loop", "mode": "framework"}


@pytest.mark.asyncio
async def test_push_browse_once_prod_keeps_deployment_skill_name(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "prod")
    runner = _runner(_FakeBot(), _FakeService(_sub(mode="framework", bot_id="bot-a")))
    await runner.push_browse_once(bot_id="bot-a", expected_mode="framework")
    sent = runner._bot.sent[0]  # type: ignore[attr-defined]

    prod_skill = browse_skill_name("prod")
    assert f"使用当前 Bot 已激活的 {prod_skill} Skill" in sent["message"]


@pytest.mark.asyncio
async def test_push_browse_once_mode_mismatch_raises_validation():
    runner = _runner(_FakeBot(), _FakeService(_sub(mode="openclaw")))
    with pytest.raises(ValidationError):
        await runner.push_browse_once(bot_id="bot-a", expected_mode="framework")


@pytest.mark.asyncio
async def test_push_browse_once_missing_subscription_raises_not_found():
    runner = _runner(_FakeBot(), _FakeService(None))
    with pytest.raises(NotFound):
        await runner.push_browse_once(bot_id="bot-a", expected_mode="framework")


@pytest.mark.asyncio
async def test_push_cron_event_only_for_openclaw(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "dev")
    runner = _runner(_FakeBot(), _FakeService(_sub(mode="framework")))
    # framework-mode subscription cannot receive cron events
    with pytest.raises(ValidationError):
        await runner.push_cron_event(bot_id="bot-a", action="register")

    runner = _runner(_FakeBot(), _FakeService(_sub(mode="openclaw", bot_id="bot-b")))
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
    assert BBS_BROWSE_LOOP_CRON_NAME in sent["message"]
    # register must forbid self-invented names and demand update-on-duplicate
    assert "固定任务名称" in sent["message"]
    assert "执行一次已激活的 forum-browse Skill" in sent["message"]
    assert "更新原任务" in sent["message"]


@pytest.mark.asyncio
async def test_push_cron_event_remove_message(monkeypatch):
    runner = _runner(_FakeBot(), _FakeService(_sub(mode="openclaw", bot_id="bot-b")))
    result = await runner.push_cron_event(bot_id="bot-b", action="remove")
    sent = runner._bot.sent[0]  # type: ignore[attr-defined]
    assert "[BBS-BROWSE-CRON]" in sent["message"]
    assert (
        "关闭" in sent["message"]
        or "删除" in sent["message"]
        or "移除" in sent["message"]
    )
    assert result.get("action") == "remove"
    assert sent["metadata"]["action"] == "remove"
    assert BBS_BROWSE_LOOP_CRON_NAME in sent["message"]
    # remove must locate the task by the fixed name (idempotent by name)
    assert "按名称" in sent["message"]


@pytest.mark.asyncio
async def test_push_cron_event_prod_register_keeps_deployment_skill_name(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "prod")
    runner = _runner(_FakeBot(), _FakeService(_sub(mode="openclaw", bot_id="bot-b")))
    await runner.push_cron_event(bot_id="bot-b", action="register")
    sent = runner._bot.sent[0]  # type: ignore[attr-defined]

    prod_skill = browse_skill_name("prod")
    assert f"执行一次已激活的 {prod_skill} Skill" in sent["message"]


@pytest.mark.asyncio
async def test_push_cron_event_register_and_remove_share_fixed_name():
    """register and remove must reference the same fixed cron-task name.

    This is the idempotency contract: a second register updates the task of
    the same name (no duplicate, e.g. no more `bbs-browse-feed-reader` next to
    `bbs-browse-30min`), and remove finds the task by that name.
    """
    runner_r = _runner(_FakeBot(), _FakeService(_sub(mode="openclaw", bot_id="bot-b")))
    await runner_r.push_cron_event(bot_id="bot-b", action="register")
    register_msg = runner_r._bot.sent[0]["message"]  # type: ignore[attr-defined]

    runner_x = _runner(_FakeBot(), _FakeService(_sub(mode="openclaw", bot_id="bot-b")))
    await runner_x.push_cron_event(bot_id="bot-b", action="remove")
    remove_msg = runner_x._bot.sent[0]["message"]  # type: ignore[attr-defined]

    assert BBS_BROWSE_LOOP_CRON_NAME in register_msg
    assert BBS_BROWSE_LOOP_CRON_NAME in remove_msg
    assert "0 * * * *" in register_msg
    # both reference the single fixed name (no per-call self-invented name)
    assert "bbs-browse-feed-reader" not in register_msg
    assert "bbs-browse-30min" not in register_msg


class _RaisingBot:
    """send_message always raises — mirrors a Bot-side delivery failure."""

    async def send_message(self, *, bot_id, message, metadata) -> BotSendResult:  # noqa: ARG002
        raise RuntimeError("bot transport down")


@pytest.mark.asyncio
async def test_push_install_bbs_skills_sends_verified_wording(monkeypatch):
    """Join-time install prompt carries all skill names and both OpenClaw paths."""

    monkeypatch.setenv("SERVER_ENV", "dev")
    runner = _runner(_FakeBot(), _FakeService(_sub(mode="openclaw", bot_id="bot-a")))
    result = await runner.push_install_bbs_skills(bot_id="bot-a")
    bot = runner._bot  # type: ignore[attr-defined]

    assert result is not None
    assert result["status"] == "submitted"
    assert result["mode"] == "openclaw"
    assert result["action"] == "install_skills"
    assert len(bot.sent) == 1
    sent = bot.sent[0]
    msg = sent["message"]
    assert msg.startswith("[BBS-BROWSE-SKILL]")
    assert (
        "forum-browse、forum-create-topic、forum-topic-handler 和 forum-digest-handler"
        in msg
    )
    for skill in bbs_browse_skill_names("dev"):
        assert skill in msg
    assert "workspace/skills/" in msg
    assert "skills-pool/skills-repo/infra/" in msg
    assert "这些 skill" in msg
    assert sent["metadata"]["action"] == "install_skills"


@pytest.mark.asyncio
async def test_push_install_bbs_skills_swallows_exception():
    """A Bot-side delivery failure must never break a join (returns None)."""

    runner = _runner(_RaisingBot(), _FakeService(_sub(mode="openclaw", bot_id="bot-a")))
    result = await runner.push_install_bbs_skills(bot_id="bot-a")
    assert result is None


def test_digest_cron_message_is_environment_selected(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "pre")
    pre_message = digest_cron_message(
        bot_id="bot-a", backend_base_url="https://bbs.test"
    )
    assert pre_message == (
        "[FORUM-DIGEST-COLLECT]\n"
        "使用 $forum-digest-handler 执行一次 Forum digest collect。"
    )

    monkeypatch.setenv("SERVER_ENV", "prod")
    prod_message = digest_cron_message(
        bot_id="bot-a", backend_base_url="https://bbs.test"
    )
    assert prod_message == (
        "[BBS-DIGEST-COLLECT]\n使用 $bbs-digest-handler 执行一次 BBS digest collect。"
    )
