"""Lifecycle and lease regression tests for the two BBS background schedulers."""

from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.task.forum.content_import.scheduler import (
    ContentImportScheduler,
)
from agentclaw.community.core.task.forum.digest.scheduler import DigestScheduler
from agentclaw.community.core.task.forum.poll_statistics.scheduler import (
    PollStatisticsScheduler,
)
from agentclaw.community.core.task.forum.topic_tag.scheduler import TopicTagScheduler
from agentclaw.community.di.bbs_digest_config import (
    BbsContentImportTaskConfig,
    BbsDigestTaskConfig,
    BbsPollStatisticsTaskConfig,
    BbsTopicTagTaskConfig,
)


class _FakeCache:
    def __init__(self, *, token: str | None = "lease-token") -> None:
        self.token = token
        self.acquire_calls: list[tuple[str, int]] = []
        self.release_calls: list[tuple[str, str]] = []

    def acquire_lock_strict(self, lock_key: str, ttl: int) -> str | None:
        self.acquire_calls.append((lock_key, ttl))
        return self.token

    def release_lock(self, lock_key: str, lock_value: str) -> bool:
        self.release_calls.append((lock_key, lock_value))
        return True

    def renew_lock_strict(self, lock_key: str, lease_token: str, ttl: int) -> bool:
        return True


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_once(self, task: Any) -> dict[str, Any]:
        self.calls.append(task.name)
        return {"task_name": task.name, "success": True, "summary": "done"}


def _digest_config(*, enabled: bool = True) -> BbsDigestTaskConfig:
    return BbsDigestTaskConfig(
        enabled=enabled,
        interval=1,
        jitter=0,
        lock_ttl=30,
        heartbeat=10,
    )


def _poll_config(*, enabled: bool = True) -> BbsPollStatisticsTaskConfig:
    return BbsPollStatisticsTaskConfig(
        enabled=enabled,
        interval=1,
        jitter=0,
        lock_ttl=30,
        heartbeat=10,
    )


def _topic_tag_config(*, enabled: bool = True) -> BbsTopicTagTaskConfig:
    return BbsTopicTagTaskConfig(
        enabled=enabled,
        interval=3600,
        jitter=30,
        lock_ttl=30,
        heartbeat=10,
    )


def _import_config(*, enabled: bool = True) -> BbsContentImportTaskConfig:
    return BbsContentImportTaskConfig(
        enabled=enabled,
        interval=1,
        jitter=0,
        lock_ttl=30,
        heartbeat=10,
    )


@pytest.mark.asyncio
async def test_both_schedulers_start_and_stop_independent_loops() -> None:
    digest_cache = _FakeCache()
    digest_executor = _FakeExecutor()
    content_cache = _FakeCache()
    content_executor = _FakeExecutor()

    digest = DigestScheduler(
        executor=digest_executor,
        cache=digest_cache,
        config=_digest_config(),
    )
    content = ContentImportScheduler(
        executor=content_executor,
        cache=content_cache,
        config=_import_config(),
    )

    await digest.startup()
    await content.startup()
    assert digest._loop_task is not None
    assert content._loop_task is not None

    await digest.shutdown()
    await content.shutdown()
    assert digest._loop_task is None
    assert content._loop_task is None
    assert digest_executor.calls == []
    assert content_executor.calls == []


@pytest.mark.asyncio
async def test_disabled_schedulers_do_not_create_loops() -> None:
    digest_cache = _FakeCache()
    content_cache = _FakeCache()
    digest = DigestScheduler(
        executor=_FakeExecutor(),
        cache=digest_cache,
        config=_digest_config(enabled=False),
    )
    content = ContentImportScheduler(
        executor=_FakeExecutor(),
        cache=content_cache,
        config=_import_config(enabled=False),
    )

    await digest.startup()
    await content.startup()
    await digest.shutdown()
    await content.shutdown()

    assert digest._loop_task is None
    assert content._loop_task is None
    assert digest_cache.acquire_calls == []
    assert content_cache.acquire_calls == []


@pytest.mark.asyncio
async def test_run_once_uses_lock_ttl_and_releases_each_independent_lock() -> None:
    digest_cache = _FakeCache()
    digest_executor = _FakeExecutor()
    content_cache = _FakeCache()
    content_executor = _FakeExecutor()
    digest = DigestScheduler(
        executor=digest_executor,
        cache=digest_cache,
        config=_digest_config(enabled=False),
    )
    content = ContentImportScheduler(
        executor=content_executor,
        cache=content_cache,
        config=_import_config(enabled=False),
    )

    digest_result = await digest.run_once()
    content_result = await content.run_once()

    assert digest_result["success"] is True
    assert digest_result["task_name"] == "digest_task"
    assert content_result["success"] is True
    assert content_result["task_name"] == "content_import_task"
    assert digest_executor.calls == ["digest_task"]
    assert content_executor.calls == ["content_import_task"]
    assert digest_cache.acquire_calls == [("bbs:summary", 30)]
    assert content_cache.acquire_calls == [("bbs:content_import", 30)]
    assert digest_cache.release_calls == [("bbs:summary", "lease-token")]
    assert content_cache.release_calls == [("bbs:content_import", "lease-token")]


@pytest.mark.asyncio
async def test_run_once_returns_without_execution_when_lock_is_held() -> None:
    cache = _FakeCache(token=None)
    executor = _FakeExecutor()
    scheduler = DigestScheduler(
        executor=executor,
        cache=cache,
        config=_digest_config(enabled=False),
    )

    result = await scheduler.run_once()

    assert result == {
        "success": False,
        "status": "lock_held",
        "lock_key": "bbs:summary",
    }
    assert cache.acquire_calls == [("bbs:summary", 30)]
    assert executor.calls == []


@pytest.mark.asyncio
async def test_poll_statistics_scheduler_uses_its_own_lease() -> None:
    cache = _FakeCache()
    executor = _FakeExecutor()
    scheduler = PollStatisticsScheduler(
        executor=executor,
        cache=cache,
        config=_poll_config(enabled=False),
    )

    await scheduler.startup()
    assert scheduler._loop_task is None

    result = await scheduler.run_once()

    assert result["success"] is True
    assert result["task_name"] == "poll_statistics_task"
    assert cache.acquire_calls == [("bbs:poll_statistics", 30)]
    assert cache.release_calls == [("bbs:poll_statistics", "lease-token")]
    assert executor.calls == ["poll_statistics_task"]


@pytest.mark.asyncio
async def test_topic_tag_scheduler_uses_its_own_lease() -> None:
    cache = _FakeCache()
    executor = _FakeExecutor()
    scheduler = TopicTagScheduler(
        executor=executor,
        cache=cache,
        config=_topic_tag_config(enabled=False),
    )

    await scheduler.startup()
    assert scheduler._loop_task is None

    result = await scheduler.run_once()

    assert result["success"] is True
    assert result["task_name"] == "topic_tag_task"
    assert cache.acquire_calls == [("bbs:topic_tags", 30)]
    assert cache.release_calls == [("bbs:topic_tags", "lease-token")]
    assert executor.calls == ["topic_tag_task"]
