"""Cross-pool claim and execution tests against the real SQLite repository."""
import asyncio
import threading
from agentclaw.community.core.task_queue.types import Complete, TaskStatus
from tests.community.core.task_queue.test_task_worker import APP, ENV, _world


def test_pool_filter_precedes_candidate_limit_and_deadline_retirement():
    world = _world()
    slow = [world.enqueue('slow') for _ in range(4)]
    expired = world.enqueue('slow', deadline_seconds=0)
    normal = world.enqueue('normal')
    outcome = world.repo.claim_batch_reporting_retired(
        worker_id='normal-worker', env=ENV, app=APP, limit=1, lease_seconds=60,
        task_types=('slow',), exclude_task_types=True,
    )
    assert [task.id for task in outcome.won] == [normal.id]
    assert not outcome.retired
    assert world.status_of(expired.id) == TaskStatus.PENDING
    assert all(world.status_of(task.id) == TaskStatus.PENDING for task in slow)
    isolated = world.repo.claim_batch_reporting_retired(
        worker_id='slow-worker', env=ENV, app=APP, limit=5, lease_seconds=60,
        task_types=('slow',),
    )
    assert {task.id for task in isolated.won} == {task.id for task in slow}
    assert [task.id for task in isolated.retired] == [expired.id]


def test_slow_pool_saturation_does_not_stop_normal_claim_or_heartbeat():
    async def scenario():
        world = _world(batch_size=4, max_concurrency=4, lease_seconds=3,
                       isolated_task_types=('slow',), isolated_concurrency=2,
                       poll_interval_seconds=0.02, poll_jitter_seconds=0)
        release = threading.Event()
        entered = threading.Event()
        count = 0
        guard = threading.Lock()

        class Slow:
            task_type = 'slow'

            def handle(self, payload):
                nonlocal count
                with guard:
                    count += 1
                    if count == 2:
                        entered.set()
                if not release.wait(10):
                    raise TimeoutError('test did not release handler')
                return Complete()

        class Normal:
            task_type = 'normal'

            def handle(self, payload):
                return Complete()

        world.registry.register(Slow())
        world.registry.register(Normal())
        slow = [world.enqueue('slow') for _ in range(4)]
        await world.worker.startup()
        try:
            assert await asyncio.to_thread(entered.wait, 4)
            normal = world.enqueue('normal')
            async def wait_for_normal():
                while world.status_of(normal.id) != TaskStatus.SUCCEEDED:
                    await asyncio.sleep(0.02)
            await asyncio.wait_for(wait_for_normal(), timeout=4)
            # A live slow handler survives more than one lease interval.
            await asyncio.sleep(3.4)
            assert world.status_of(slow[0].id) == TaskStatus.RUNNING
            assert world.repo.get_by_id(slow[0].id).attempts == 1
            assert count == 2
        finally:
            release.set()
            await world.worker.shutdown()

    asyncio.run(scenario())


def test_isolation_requires_valid_capacity_and_type_configuration():
    import pytest

    with pytest.raises(ValueError):
        _world(max_concurrency=2, isolated_task_types=('slow',), isolated_concurrency=2)
    with pytest.raises(ValueError):
        _world(isolated_concurrency=-1)
    with pytest.raises(ValueError):
        _world(isolated_task_types=('slow', 'slow'), isolated_concurrency=1)


def test_reclaimed_task_rejects_previous_holders_outcome():
    world = _world()
    task = world.enqueue('slow')
    first = world.repo.claim_batch_reporting_retired(
        worker_id='first', env=ENV, app=APP, limit=1, lease_seconds=0,
        task_types=('slow',),
    )
    second = world.repo.claim_batch_reporting_retired(
        worker_id='second', env=ENV, app=APP, limit=1, lease_seconds=60,
        task_types=('slow',),
    )
    assert [item.id for item in first.won] == [task.id]
    assert [item.id for item in second.won] == [task.id]
    assert world.repo.complete(task_id=task.id, worker_id='first') is False
    assert world.repo.complete(task_id=task.id, worker_id='second') is True


def test_worker_configuration_parses_default_and_isolated_policy():
    import pytest
    from agentclaw.community.di.task_queue_worker_config import parse_task_queue_worker

    default = parse_task_queue_worker({})
    assert default.isolated_task_types == () and default.isolated_concurrency == 0
    policy = parse_task_queue_worker({
        'batch_size': 4, 'max_concurrency': 4,
        'isolated_task_types': ['slow'], 'isolated_concurrency': 2,
    })
    assert policy.isolated_task_types == ('slow',) and policy.isolated_concurrency == 2
    with pytest.raises(ValueError):
        parse_task_queue_worker({'isolated_task_types': 'slow', 'isolated_concurrency': 1})


def test_shutdown_timeout_does_not_cancel_running_handler_or_lease(monkeypatch):
    import agentclaw.community.core.task_queue.services.worker as worker_module

    monkeypatch.setattr(worker_module, 'ISOLATED_SHUTDOWN_SECONDS', 0.05)

    async def scenario():
        world = _world(batch_size=2, max_concurrency=2, lease_seconds=3,
                       isolated_task_types=('slow',), isolated_concurrency=1,
                       poll_interval_seconds=0.02, poll_jitter_seconds=0)
        entered, release = threading.Event(), threading.Event()

        class Slow:
            task_type = 'slow'

            def handle(self, payload):
                entered.set()
                if not release.wait(10):
                    raise TimeoutError('test did not release handler')
                return Complete()

        world.registry.register(Slow())
        task = world.enqueue('slow')
        await world.worker.startup()
        try:
            assert await asyncio.to_thread(entered.wait, 4)
            await world.worker.shutdown()
            assert not world.worker._task.done()
            await asyncio.sleep(3.2)
            assert world.status_of(task.id) == TaskStatus.RUNNING
            assert world.repo.get_by_id(task.id).attempts == 1
        finally:
            release.set()
            await asyncio.wait_for(world.worker._task, timeout=5)
        assert world.status_of(task.id) == TaskStatus.SUCCEEDED

    asyncio.run(scenario())
