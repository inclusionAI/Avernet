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


def test_both_pools_enforce_the_combined_handler_limit():
    async def scenario():
        world = _world(batch_size=4, max_concurrency=4, lease_seconds=3,
                       isolated_task_types=('slow',), isolated_concurrency=2,
                       poll_interval_seconds=0.02, poll_jitter_seconds=0)
        release, all_started = threading.Event(), threading.Event()
        lock = threading.Lock()
        counts = {'slow': 0, 'normal': 0, 'current': 0, 'max': 0}

        class Handler:
            def __init__(self, task_type):
                self.task_type = task_type

            def handle(self, payload):
                with lock:
                    counts[self.task_type] += 1
                    counts['current'] += 1
                    counts['max'] = max(counts['max'], counts['current'])
                    if counts['current'] == 4:
                        all_started.set()
                try:
                    if not release.wait(10):
                        raise TimeoutError('test did not release handlers')
                    return Complete()
                finally:
                    with lock:
                        counts['current'] -= 1

        world.registry.register(Handler('slow'))
        world.registry.register(Handler('normal'))
        for name in ('slow', 'normal'):
            for _ in range(3):
                world.enqueue(name)
        await world.worker.startup()
        try:
            assert await asyncio.to_thread(all_started.wait, 5)
            await asyncio.sleep(0.15)
            assert counts['slow'] == counts['normal'] == 2
            assert counts['max'] == 4
        finally:
            release.set()
            await world.worker.shutdown()
        assert counts['max'] <= 4

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


def test_invalid_isolation_configuration_fails_injector_build():
    import pytest
    from unittest.mock import patch
    from agentclaw.community.di import DeployProfile, build_injector

    with patch('agentclaw.community.di.modules.config_module._user_config',
               return_value={'task_queue_worker': {'isolated_concurrency': 1}}):
        with pytest.raises(ValueError):
            build_injector(profile=DeployProfile.TEST)


def test_shutdown_waits_for_running_handler_and_lease():
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
        shutdown = None
        try:
            assert await asyncio.to_thread(entered.wait, 4)
            shutdown = asyncio.create_task(world.worker.shutdown())
            await asyncio.sleep(3.2)
            assert not shutdown.done()
            assert world.status_of(task.id) == TaskStatus.RUNNING
            assert world.repo.get_by_id(task.id).attempts == 1
        finally:
            release.set()
            if shutdown is not None:
                await asyncio.wait_for(shutdown, timeout=5)
            else:
                await world.worker.shutdown()
        assert world.status_of(task.id) == TaskStatus.SUCCEEDED

    asyncio.run(scenario())


def test_isolated_executors_preserve_each_tasks_trace():
    from agentclaw.community.plugins.community.tracer import CommunityTracer

    async def scenario():
        world = _world(batch_size=2, max_concurrency=2,
                       isolated_task_types=('slow',), isolated_concurrency=1,
                       poll_interval_seconds=0.02, poll_jitter_seconds=0)
        tracer = CommunityTracer()
        world.worker._tracer = world.service._tracer = tracer
        seen = {}

        class Handler:
            def __init__(self, task_type):
                self.task_type = task_type

            def handle(self, payload):
                seen[self.task_type] = tracer.current_trace_id()
                return Complete()

        world.registry.register(Handler('slow'))
        world.registry.register(Handler('normal'))
        with tracer.trace_scope({'trace_id': 'slow-trace'}):
            slow = world.enqueue('slow')
        with tracer.trace_scope({'trace_id': 'normal-trace'}):
            normal = world.enqueue('normal')
        await world.worker.startup()
        try:
            async def wait_until_done():
                while any(world.status_of(t.id) != TaskStatus.SUCCEEDED for t in (slow, normal)):
                    await asyncio.sleep(0.02)
            await asyncio.wait_for(wait_until_done(), 5)
        finally:
            await world.worker.shutdown()
        assert seen == {'slow': 'slow-trace', 'normal': 'normal-trace'}

    asyncio.run(scenario())


def test_claim_completed_after_drain_is_returned_without_starting_handler():
    async def scenario():
        world = _world(batch_size=2, max_concurrency=2, lease_seconds=3,
                       isolated_task_types=('slow',), isolated_concurrency=1,
                       poll_interval_seconds=0.02, poll_jitter_seconds=0)
        claimed, release, executed = threading.Event(), threading.Event(), threading.Event()
        original = world.repo.claim_batch_reporting_retired

        def claim_batch_reporting_retired(**kwargs):
            result = original(**kwargs)
            if result.won:
                claimed.set()
                if not release.wait(10):
                    raise TimeoutError('test did not release claim')
            return result

        class Slow:
            task_type = 'slow'

            def handle(self, payload):
                executed.set()
                return Complete()

        world.repo.claim_batch_reporting_retired = claim_batch_reporting_retired
        world.registry.register(Slow())
        task = world.enqueue('slow')
        await world.worker.startup()
        drain = None
        try:
            assert await asyncio.to_thread(claimed.wait, 4)
            drain = asyncio.create_task(world.worker.drain_before_shutdown())
            await asyncio.sleep(0.05)
            assert not drain.done()
            release.set()
            await asyncio.wait_for(drain, 5)
            assert not executed.is_set()
            assert world.status_of(task.id) == TaskStatus.PENDING
        finally:
            release.set()
            if drain is not None:
                await drain
            else:
                await world.worker.shutdown()

    asyncio.run(scenario())


def test_claim_waiting_for_execution_slot_is_returned_after_drain():
    async def scenario():
        world = _world(batch_size=2, max_concurrency=2, lease_seconds=5,
                       isolated_task_types=('slow',), isolated_concurrency=1,
                       poll_interval_seconds=0.02, poll_jitter_seconds=0)
        executed = threading.Event()

        class Slow:
            task_type = 'slow'

            def handle(self, payload):
                executed.set()
                return Complete()

        world.registry.register(Slow())
        task = world.enqueue('slow')
        await world.worker._sem.acquire()
        await world.worker._sem.acquire()
        await world.worker.startup()
        drain = None
        try:
            async def wait_for_claim():
                while world.status_of(task.id) != TaskStatus.RUNNING:
                    await asyncio.sleep(0.02)
            await asyncio.wait_for(wait_for_claim(), 4)
            drain = asyncio.create_task(world.worker.drain_before_shutdown())
            await asyncio.sleep(0.05)
        finally:
            world.worker._sem.release()
            world.worker._sem.release()
            if drain is not None:
                await asyncio.wait_for(drain, 5)
            else:
                await world.worker.shutdown()
        assert not executed.is_set()
        assert world.status_of(task.id) == TaskStatus.PENDING

    asyncio.run(scenario())


def test_queued_claim_is_skipped_after_drain_starts():
    async def scenario():
        world = _world(batch_size=2, max_concurrency=2, lease_seconds=3,
                       isolated_task_types=('slow',), isolated_concurrency=1,
                       poll_interval_seconds=0.02, poll_jitter_seconds=0)
        entered, release, executed = threading.Event(), threading.Event(), threading.Event()

        class Slow:
            task_type = 'slow'

            def handle(self, payload):
                executed.set()
                return Complete()

        def block_db():
            entered.set()
            if not release.wait(10):
                raise TimeoutError('test did not release database worker')

        world.registry.register(Slow())
        await world.worker.startup()
        block = asyncio.create_task(world.worker._db(block_db))
        drain = None
        try:
            assert await asyncio.to_thread(entered.wait, 4)
            task = world.enqueue('slow')
            await asyncio.sleep(0.1)  # let both pools queue their claim calls
            drain = asyncio.create_task(world.worker.drain_before_shutdown())
            await asyncio.sleep(0.05)
            release.set()
            await asyncio.wait_for(block, 5)
            await asyncio.wait_for(drain, 5)
            assert not executed.is_set()
            assert world.status_of(task.id) == TaskStatus.PENDING
            assert world.repo.get_by_id(task.id).attempts == 0
        finally:
            release.set()
            if drain is not None:
                await drain
            else:
                await world.worker.shutdown()

    asyncio.run(scenario())


def test_claim_return_database_error_makes_drain_fail():
    import pytest

    async def scenario():
        world = _world(batch_size=2, max_concurrency=2, lease_seconds=3,
                       isolated_task_types=('slow',), isolated_concurrency=1,
                       poll_interval_seconds=0.02, poll_jitter_seconds=0)
        claimed, release, executed = threading.Event(), threading.Event(), threading.Event()
        original = world.repo.claim_batch_reporting_retired

        def claim_batch_reporting_retired(**kwargs):
            result = original(**kwargs)
            if result.won:
                claimed.set()
                if not release.wait(10):
                    raise TimeoutError('test did not release claim')
            return result

        def fail_return(**kwargs):
            raise RuntimeError('database result unknown')

        class Slow:
            task_type = 'slow'

            def handle(self, payload):
                executed.set()
                return Complete()

        world.repo.claim_batch_reporting_retired = claim_batch_reporting_retired
        world.repo.reschedule = fail_return
        world.registry.register(Slow())
        world.enqueue('slow')
        await world.worker.startup()
        assert await asyncio.to_thread(claimed.wait, 4)
        drain = asyncio.create_task(world.worker.drain_before_shutdown())
        await asyncio.sleep(0.05)
        release.set()
        with pytest.raises(RuntimeError, match='database result unknown'):
            await asyncio.wait_for(drain, 5)
        assert not executed.is_set()

    asyncio.run(scenario())


def test_claim_committed_but_response_lost_makes_drain_fail():
    import pytest

    async def scenario():
        world = _world(batch_size=2, max_concurrency=2, lease_seconds=3,
                       isolated_task_types=('slow',), isolated_concurrency=1,
                       poll_interval_seconds=0.02, poll_jitter_seconds=0)
        committed, release, executed = threading.Event(), threading.Event(), threading.Event()
        original = world.repo.claim_batch_reporting_retired

        def claim_batch_reporting_retired(**kwargs):
            result = original(**kwargs)
            if result.won:
                committed.set()
                if not release.wait(10):
                    raise TimeoutError('test did not release claim')
                raise RuntimeError('claim response lost')
            return result

        class Slow:
            task_type = 'slow'

            def handle(self, payload):
                executed.set()
                return Complete()

        world.repo.claim_batch_reporting_retired = claim_batch_reporting_retired
        world.registry.register(Slow())
        task = world.enqueue('slow')
        await world.worker.startup()
        assert await asyncio.to_thread(committed.wait, 4)
        drain = asyncio.create_task(world.worker.drain_before_shutdown())
        await asyncio.sleep(0.05)
        release.set()
        with pytest.raises(RuntimeError, match='claim response lost'):
            await asyncio.wait_for(drain, 5)
        assert not executed.is_set()
        assert world.status_of(task.id) == TaskStatus.RUNNING

    asyncio.run(scenario())


def test_claim_expiring_during_drain_runs_give_up_without_handler():
    async def scenario():
        world = _world(batch_size=2, max_concurrency=2, lease_seconds=5,
                       isolated_task_types=('slow',), isolated_concurrency=1,
                       poll_interval_seconds=0.02, poll_jitter_seconds=0)
        claimed, release, executed, gave_up = (threading.Event() for _ in range(4))
        original = world.repo.claim_batch_reporting_retired

        def claim_batch_reporting_retired(**kwargs):
            result = original(**kwargs)
            if result.won:
                claimed.set()
                if not release.wait(10):
                    raise TimeoutError('test did not release claim')
            return result

        class Slow:
            task_type = 'slow'

            def handle(self, payload):
                executed.set()
                return Complete()

            def on_give_up(self, task):
                gave_up.set()

        world.repo.claim_batch_reporting_retired = claim_batch_reporting_retired
        world.registry.register(Slow())
        task = world.enqueue('slow', deadline_seconds=2)
        await world.worker.startup()
        drain = None
        try:
            assert await asyncio.to_thread(claimed.wait, 4)
            drain = asyncio.create_task(world.worker.drain_before_shutdown())
            await asyncio.sleep(2.2)
            release.set()
            await asyncio.wait_for(drain, 5)
            assert not executed.is_set()
            assert gave_up.is_set()
            assert world.status_of(task.id) == TaskStatus.TIMED_OUT
        finally:
            release.set()
            if drain is not None:
                await drain
            else:
                await world.worker.shutdown()

    asyncio.run(scenario())


def test_slow_expiry_hook_does_not_delay_claimed_task_heartbeat():
    async def scenario():
        world = _world(batch_size=4, max_concurrency=4, lease_seconds=3,
                       isolated_task_types=('slow',), isolated_concurrency=2,
                       poll_interval_seconds=0.02, poll_jitter_seconds=0)
        hook_started, run_started, release = threading.Event(), threading.Event(), threading.Event()

        class Slow:
            task_type = 'slow'

            def handle(self, payload):
                run_started.set()
                if not release.wait(10):
                    raise TimeoutError('test did not release handler')
                return Complete()

            def on_give_up(self, task):
                hook_started.set()
                if not release.wait(10):
                    raise TimeoutError('test did not release hook')

        world.registry.register(Slow())
        world.enqueue('slow', deadline_seconds=0)
        task = world.enqueue('slow')
        await world.worker.startup()
        try:
            assert await asyncio.to_thread(hook_started.wait, 4)
            assert await asyncio.to_thread(run_started.wait, 4)
            await asyncio.sleep(3.2)
            assert world.status_of(task.id) == TaskStatus.RUNNING
            assert world.repo.get_by_id(task.id).attempts == 1
        finally:
            release.set()
            await world.worker.shutdown()

    asyncio.run(scenario())


def test_lifespan_drains_before_other_dependencies_shutdown(monkeypatch):
    import importlib
    from agentclaw.community.kernel.lifecycle import LifecycleBase

    app_module = importlib.import_module('agentclaw.community.adapters.http.app')

    async def scenario():
        world = _world(batch_size=2, max_concurrency=2, lease_seconds=3,
                       isolated_task_types=('slow',), isolated_concurrency=1,
                       poll_interval_seconds=0.02, poll_jitter_seconds=0)
        entered, release = threading.Event(), threading.Event()
        events = []

        class Slow:
            task_type = 'slow'

            def handle(self, payload):
                entered.set()
                if not release.wait(10):
                    raise TimeoutError('test did not release handler')
                events.append('handler-finished')
                return Complete()

        class Dependency(LifecycleBase):
            async def shutdown(self):
                events.append('dependency-shutdown')

            async def teardown(self):
                events.append('dependency-teardown')

        world.registry.register(Slow())
        monkeypatch.setattr(app_module, 'require_worker_injector', lambda app: None)
        monkeypatch.setattr(app_module, 'discover_lifecycle_participants',
                            lambda injector: [world.worker, Dependency()])
        context = app_module._app_lifespan(object())
        await context.__aenter__()
        exit_task = None
        try:
            world.enqueue('slow')
            assert await asyncio.to_thread(entered.wait, 4)
            exit_task = asyncio.create_task(context.__aexit__(None, None, None))
            await asyncio.sleep(0.1)
            assert not exit_task.done()
            assert not events
        finally:
            release.set()
            if exit_task is not None:
                await asyncio.wait_for(exit_task, 5)
            else:
                await context.__aexit__(None, None, None)
        assert events == ['handler-finished', 'dependency-shutdown', 'dependency-teardown']

    asyncio.run(scenario())


def test_lifespan_waits_for_slow_give_up_and_renews_claim(monkeypatch):
    import importlib
    from agentclaw.community.kernel.lifecycle import LifecycleBase

    app_module = importlib.import_module('agentclaw.community.adapters.http.app')

    async def scenario():
        world = _world(batch_size=4, max_concurrency=4, lease_seconds=3,
                       isolated_task_types=('slow',), isolated_concurrency=2,
                       poll_interval_seconds=0.02, poll_jitter_seconds=0)
        hook_started, handler_started, release = (threading.Event() for _ in range(3))
        events = []

        class Slow:
            task_type = 'slow'

            def handle(self, payload):
                handler_started.set()
                if not release.wait(10):
                    raise TimeoutError('test did not release handler')
                return Complete()

            def on_give_up(self, task):
                hook_started.set()
                if not release.wait(10):
                    raise TimeoutError('test did not release hook')

        class Dependency(LifecycleBase):
            async def shutdown(self):
                events.append('shutdown')

        world.registry.register(Slow())
        monkeypatch.setattr(app_module, 'require_worker_injector', lambda app: None)
        monkeypatch.setattr(app_module, 'discover_lifecycle_participants',
                            lambda injector: [world.worker, Dependency()])
        context = app_module._app_lifespan(object())
        await context.__aenter__()
        stop = None
        try:
            world.enqueue('slow', deadline_seconds=0)
            task = world.enqueue('slow')
            assert await asyncio.to_thread(hook_started.wait, 4)
            assert await asyncio.to_thread(handler_started.wait, 4)
            stop = asyncio.create_task(context.__aexit__(None, None, None))
            await asyncio.sleep(3.2)
            assert not stop.done() and not events
            assert world.status_of(task.id) == TaskStatus.RUNNING
            assert world.repo.get_by_id(task.id).attempts == 1
        finally:
            release.set()
            if stop is not None:
                await asyncio.wait_for(stop, 5)
            else:
                await context.__aexit__(None, None, None)
        assert events == ['shutdown']
        assert world.status_of(task.id) == TaskStatus.SUCCEEDED

    asyncio.run(scenario())


def test_failed_lifespan_drain_does_not_run_shutdown_or_teardown(monkeypatch):
    import importlib
    import pytest
    from agentclaw.community.kernel.lifecycle import LifecycleBase

    app_module = importlib.import_module('agentclaw.community.adapters.http.app')
    events = []

    class FailingDrain(LifecycleBase):
        async def drain_before_shutdown(self):
            raise RuntimeError('drain failed')

        async def shutdown(self):
            events.append('shutdown')

        async def teardown(self):
            events.append('teardown')

    monkeypatch.setattr(app_module, 'require_worker_injector', lambda app: None)
    monkeypatch.setattr(app_module, 'discover_lifecycle_participants', lambda injector: [FailingDrain()])

    async def scenario():
        with pytest.raises(RuntimeError, match='drain failed'):
            async with app_module._app_lifespan(object()):
                pass
        assert events == []

    asyncio.run(scenario())


def test_failed_drain_still_waits_for_other_drain(monkeypatch):
    import importlib
    import pytest
    from agentclaw.community.kernel.lifecycle import LifecycleBase

    app_module = importlib.import_module('agentclaw.community.adapters.http.app')

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Fails(LifecycleBase):
            async def drain_before_shutdown(self):
                raise RuntimeError('drain failed')

        class Waits(LifecycleBase):
            async def drain_before_shutdown(self):
                entered.set()
                await release.wait()

        monkeypatch.setattr(app_module, 'require_worker_injector', lambda app: None)
        monkeypatch.setattr(app_module, 'discover_lifecycle_participants',
                            lambda injector: [Fails(), Waits()])
        context = app_module._app_lifespan(object())
        await context.__aenter__()
        stop = asyncio.create_task(context.__aexit__(None, None, None))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.sleep(0)
            assert not stop.done()
        finally:
            release.set()
        with pytest.raises(RuntimeError, match='drain failed'):
            await asyncio.wait_for(stop, 2)

    asyncio.run(scenario())


def test_cancelled_lifespan_drain_does_not_run_teardown(monkeypatch):
    import importlib
    import pytest
    from agentclaw.community.kernel.lifecycle import LifecycleBase

    app_module = importlib.import_module('agentclaw.community.adapters.http.app')

    async def scenario():
        entered, events = asyncio.Event(), []

        class Blocking(LifecycleBase):
            async def drain_before_shutdown(self):
                entered.set()
                await asyncio.Event().wait()

            async def shutdown(self):
                events.append('shutdown')

            async def teardown(self):
                events.append('teardown')

        monkeypatch.setattr(app_module, 'require_worker_injector', lambda app: None)
        monkeypatch.setattr(app_module, 'discover_lifecycle_participants',
                            lambda injector: [Blocking()])
        context = app_module._app_lifespan(object())
        await context.__aenter__()
        stop = asyncio.create_task(context.__aexit__(None, None, None))
        await asyncio.wait_for(entered.wait(), 2)
        stop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop
        assert events == []

    asyncio.run(scenario())
