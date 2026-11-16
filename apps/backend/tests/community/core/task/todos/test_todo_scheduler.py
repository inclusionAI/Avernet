from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.core.task.todos.scheduler import TodoScheduler
from agentclaw.community.di.todo_config import TodoSchedulerConfig

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def _todo(todo_id: str, status: TodoStatus) -> TodoRecord:
    return TodoRecord(
        todo_id=todo_id,
        space_id=7,
        title=todo_id,
        description="",
        bot_id="bot_1",
        bot_name="Bot One",
        status=status,
        collab_context_id=None,
        created_by="creator_1",
        created_at=NOW,
        updated_at=NOW,
    )


def _config(*, enabled: bool = False, heartbeat: float = 10) -> TodoSchedulerConfig:
    return TodoSchedulerConfig(
        enabled=enabled,
        interval=60,
        batch_size=2,
        jitter=0,
        lock_ttl=30,
        heartbeat=heartbeat,  # type: ignore[arg-type]
    )


class _Repository:
    def __init__(
        self,
        *,
        pending: tuple[TodoRecord, ...] = (),
        pending_acceptance: tuple[TodoRecord, ...] = (),
        in_progress: tuple[TodoRecord, ...] = (),
    ) -> None:
        self.rows = {
            TodoStatus.PENDING: pending,
            TodoStatus.PENDING_ACCEPTANCE: pending_acceptance,
            TodoStatus.IN_PROGRESS: in_progress,
        }
        self.calls: list[tuple[TodoStatus, int]] = []

    def list_todos_by_status(self, *, status: TodoStatus, limit: int):
        self.calls.append((status, limit))
        return self.rows[status][:limit]


class _Execution:
    def __init__(
        self,
        *,
        failures: set[str] | None = None,
        delay: float = 0,
        refresh_result: TodoStatus = TodoStatus.PENDING_ACCEPTANCE,
    ) -> None:
        self.failures = failures or set()
        self.delay = delay
        self.refresh_result = refresh_result
        self.calls: list[tuple[str, str]] = []

    async def dispatch_pending(self, *, todo_id: str, env: str | None = None):
        self.calls.append(("dispatch", todo_id))
        if self.delay:
            await asyncio.sleep(self.delay)
        if todo_id in self.failures:
            raise RuntimeError(f"dispatch {todo_id} failed")
        return replace(
            _todo(todo_id, TodoStatus.PENDING), status=TodoStatus.IN_PROGRESS
        )

    async def reconcile_pending_acceptance(self, *, todo_id: str):
        self.calls.append(("reconcile", todo_id))
        if self.delay:
            await asyncio.sleep(self.delay)
        if todo_id in self.failures:
            raise RuntimeError(f"reconcile {todo_id} failed")
        return replace(
            _todo(todo_id, TodoStatus.PENDING_ACCEPTANCE),
            status=TodoStatus.COMPLETED,
        )

    async def refresh_in_progress(self, *, todo_id: str):
        self.calls.append(("refresh", todo_id))
        if self.delay:
            await asyncio.sleep(self.delay)
        if todo_id in self.failures:
            raise RuntimeError(f"refresh {todo_id} failed")
        return replace(
            _todo(todo_id, TodoStatus.IN_PROGRESS),
            status=self.refresh_result,
        )

    async def resume_claimed_interaction(self, *, todo_id: str):
        self.calls.append(("resume", todo_id))
        return replace(_todo(todo_id, TodoStatus.IN_PROGRESS))


class _Cache:
    def __init__(
        self,
        *,
        token: str | None = "lease-token",
        renew_result: bool = True,
        renew_error: Exception | None = None,
    ) -> None:
        self.token = token
        self.renew_result = renew_result
        self.renew_error = renew_error
        self.acquire_calls: list[tuple[str, int]] = []
        self.renew_calls: list[tuple[str, str, int]] = []
        self.release_calls: list[tuple[str, str]] = []

    def acquire_lock_strict(self, lock_key: str, ttl: int):
        self.acquire_calls.append((lock_key, ttl))
        return self.token

    def renew_lock_strict(self, lock_key: str, token: str, ttl: int):
        self.renew_calls.append((lock_key, token, ttl))
        if self.renew_error is not None:
            raise self.renew_error
        return self.renew_result

    def release_lock(self, lock_key: str, token: str):
        self.release_calls.append((lock_key, token))
        return True


@pytest.mark.asyncio
async def test_lock_held_skips_repository_scan():
    repository = _Repository(pending=(_todo("p1", TodoStatus.PENDING),))
    execution = _Execution()
    cache = _Cache(token=None)
    scheduler = TodoScheduler(repository, execution, cache, _config())

    result = await scheduler.run_once()

    assert result["status"] == "lock_held"
    assert cache.acquire_calls == [("todo:scheduler", 30)]
    assert repository.calls == []
    assert execution.calls == []


@pytest.mark.asyncio
async def test_tick_scans_each_status_with_independent_batch_limit_in_order():
    repository = _Repository(
        pending=tuple(_todo(f"p{index}", TodoStatus.PENDING) for index in range(1, 4)),
        pending_acceptance=tuple(
            _todo(f"a{index}", TodoStatus.PENDING_ACCEPTANCE)
            for index in range(1, 4)
        ),
        in_progress=tuple(
            _todo(f"i{index}", TodoStatus.IN_PROGRESS) for index in range(1, 4)
        ),
    )
    execution = _Execution()
    cache = _Cache()
    scheduler = TodoScheduler(repository, execution, cache, _config())

    result = await scheduler.run_once()

    assert result["status"] == "executed"
    assert repository.calls == [
        (TodoStatus.PENDING, 2),
        (TodoStatus.PENDING_ACCEPTANCE, 2),
        (TodoStatus.IN_PROGRESS, 2),
    ]
    assert execution.calls == [
        ("dispatch", "p1"),
        ("dispatch", "p2"),
        ("reconcile", "a1"),
        ("reconcile", "a2"),
        ("refresh", "i1"),
        ("refresh", "i2"),
    ]
    assert cache.release_calls == [("todo:scheduler", "lease-token")]


@pytest.mark.asyncio
async def test_scheduler_resumes_claimed_interaction_after_refresh_leaves_it_running():
    repository = _Repository(
        in_progress=(_todo("i1", TodoStatus.IN_PROGRESS),),
    )
    execution = _Execution(refresh_result=TodoStatus.IN_PROGRESS)
    scheduler = TodoScheduler(repository, execution, _Cache(), _config())

    result = await scheduler.run_once()

    assert result["processed"] == 1
    assert execution.calls == [("refresh", "i1"), ("resume", "i1")]


@pytest.mark.asyncio
async def test_single_todo_failures_do_not_stop_remaining_work():
    repository = _Repository(
        pending=(
            _todo("p1", TodoStatus.PENDING),
            _todo("p2", TodoStatus.PENDING),
        ),
        in_progress=(
            _todo("i1", TodoStatus.IN_PROGRESS),
            _todo("i2", TodoStatus.IN_PROGRESS),
        ),
    )
    execution = _Execution(failures={"p1", "i1"})
    scheduler = TodoScheduler(repository, execution, _Cache(), _config())

    result = await scheduler.run_once()

    assert result["errors"] == 2
    assert execution.calls == [
        ("dispatch", "p1"),
        ("dispatch", "p2"),
        ("refresh", "i1"),
        ("refresh", "i2"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cache",
    [
        _Cache(renew_result=False),
        _Cache(renew_error=RuntimeError("cache unavailable")),
    ],
)
async def test_lease_loss_stops_remaining_todos_and_releases_original_token(cache):
    repository = _Repository(
        pending=(
            _todo("p1", TodoStatus.PENDING),
            _todo("p2", TodoStatus.PENDING),
        ),
        in_progress=(_todo("i1", TodoStatus.IN_PROGRESS),),
    )
    execution = _Execution(delay=0.03)
    scheduler = TodoScheduler(
        repository,
        execution,
        cache,
        _config(heartbeat=0.01),
    )

    result = await scheduler.run_once()

    assert result["status"] == "lease_lost"
    assert execution.calls == [("dispatch", "p1")]
    assert repository.calls == [(TodoStatus.PENDING, 2)]
    assert cache.renew_calls
    assert cache.release_calls == [("todo:scheduler", "lease-token")]


@pytest.mark.asyncio
async def test_disabled_lifecycle_does_not_start_loop():
    scheduler = TodoScheduler(_Repository(), _Execution(), _Cache(), _config())

    await scheduler.startup()
    await scheduler.shutdown()

    assert scheduler._loop_task is None


@pytest.mark.asyncio
async def test_enabled_lifecycle_starts_and_shutdown_cancels_loop():
    scheduler = TodoScheduler(
        _Repository(),
        _Execution(),
        _Cache(),
        _config(enabled=True),
    )

    await scheduler.startup()
    assert scheduler._loop_task is not None

    await scheduler.shutdown()
    assert scheduler._loop_task is None


@pytest.mark.asyncio
async def test_loop_survives_one_failed_tick(monkeypatch: pytest.MonkeyPatch):
    scheduler = TodoScheduler(_Repository(), _Execution(), _Cache(), _config())
    calls = 0

    async def no_sleep(_delay: float) -> None:
        return None

    async def run_once() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("one bad tick")
        scheduler._started = False
        return {"status": "executed"}

    monkeypatch.setattr(
        "agentclaw.community.core.task.todos.scheduler.asyncio.sleep",
        no_sleep,
    )
    monkeypatch.setattr(scheduler, "run_once", run_once)
    scheduler._started = True

    await scheduler._loop()

    assert calls == 2
