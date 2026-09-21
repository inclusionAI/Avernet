"""Regression tests for Browse Loop scheduler lifecycle startup."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentclaw.community.core.forum.browsing import scheduler as scheduler_module
from agentclaw.community.core.forum.browsing.scheduler import BbsBrowseLoopScheduler
from agentclaw.community.core.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    BrowseSubscriptionPage,
    BrowseSubscriptionRecord,
)
from agentclaw.community.core.forum.services.forum_service import ForumService


def _subscription(index: int) -> BrowseSubscriptionRecord:
    now = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    return BrowseSubscriptionRecord(
        bot_id=f"bot-{index:03d}",
        owner_user_id="111111",
        mode=BROWSE_MODE_FRAMEWORK,
        note=None,
        created_at=now,
        updated_at=now,
    )


class _FakeRepository:
    def __init__(self, count: int) -> None:
        self._items = tuple(_subscription(index) for index in range(count))
        self.calls: list[dict[str, object]] = []

    def list_subscriptions(self, *, offset: int, limit: int, mode: str | None = None):
        self.calls.append({"offset": offset, "limit": limit, "mode": mode})
        return BrowseSubscriptionPage(
            total=len(self._items),
            items=self._items[offset : offset + limit],
        )


class _FakeBackgroundScheduler:
    def __init__(self) -> None:
        self.started = False
        self.jobs: list[dict[str, object]] = []

    def start(self) -> None:
        self.started = True

    def add_job(self, func, trigger, **kwargs) -> None:
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})

    def shutdown(self, *, wait: bool) -> None:
        self.started = False


@pytest.mark.asyncio
async def test_startup_restores_all_subscriptions_with_valid_page_size(monkeypatch):
    repository = _FakeRepository(count=101)
    service = ForumService(repository)
    fake_scheduler = _FakeBackgroundScheduler()
    monkeypatch.setattr(
        scheduler_module,
        "BackgroundScheduler",
        lambda: fake_scheduler,
    )

    scheduler = BbsBrowseLoopScheduler(runner=object(), service=service)
    await scheduler.startup()

    assert repository.calls == [
        {"offset": 0, "limit": 100, "mode": BROWSE_MODE_FRAMEWORK},
        {"offset": 100, "limit": 100, "mode": BROWSE_MODE_FRAMEWORK},
    ]
    assert fake_scheduler.started is True
    assert len(fake_scheduler.jobs) == 101
    assert {job["id"] for job in fake_scheduler.jobs} == {
        f"bbs-browse-framework-bot-{index:03d}" for index in range(101)
    }
