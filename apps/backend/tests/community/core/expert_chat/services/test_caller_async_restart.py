"""Caller restart admission, queue replay, polling and engine compatibility."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.caller_restart import (
    KEY, AicodingCallerRestartHandler, AicodingCallerRestartLifecycle,
    CallerRestartState, _key,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_backup import RestartBackupError
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule
from tests.community.core.expert_chat.services.test_expert_chat_instance_service import (
    _make_service, _wire_publish, _wire_bot_repo, BOT_ID, OWNER_ID, BOT_UUID,
)

IDS = dict(user_id='caller-user', bot_id=BOT_ID, owner_id=OWNER_ID)


class MemoryInstances:
    def __init__(self):
        self.row = {**IDS, 'status': 'success', 'ext': {
            'bot_uuid': BOT_UUID, 'version': 1, 'baas_publish_id': 10, 'binding_id': 12,
        }}
        self.writes = []
        self.fail_next = False

    def get_instance(self, *args, **kwargs):
        return deepcopy(self.row)

    def update_instance(self, **kwargs):
        self.writes.append(deepcopy(kwargs))
        if self.fail_next:
            self.fail_next = False
            return False
        for key in ('status', 'ext'):
            if kwargs.get(key) is not None:
                self.row[key] = deepcopy(kwargs[key])
        return True


class MemoryLocks:
    def __init__(self):
        self.row = None
        self.serial = 0

    def acquire(self, **kwargs):
        if self.row is not None:
            return None
        self.serial += 1
        value = str(self.serial)
        self.row = SimpleNamespace(lock_token=value)
        return self.row

    def release(self, *, lock_token, **kwargs):
        if self.row and self.row.lock_token == lock_token:
            self.row = None
            return True
        return False

    def get(self, **kwargs):
        return self.row

    def get_if_stale(self, **kwargs):
        return None


@pytest.fixture
def world():
    svc, _, baas, publishes, bots, bindings, build, *_ = _make_service()
    _wire_publish(publishes)
    _wire_bot_repo(bots, {'bot_id': BOT_ID, 'owner_id': OWNER_ID, 'active_engine': 'aicoding'})
    instances = MemoryInstances()
    svc._instance_repo = instances
    svc._restart_locks = MemoryLocks()
    tasks = []

    def enqueue(task_type, payload, **kwargs):
        task = SimpleNamespace(status='PENDING', payload=deepcopy(payload))
        tasks.append(task)
        assert instances.row['status'] == 'init'  # before the worker can see the task
        return task, True

    svc._task_queue.enqueue.side_effect = enqueue
    svc._task_queue.find_by_idempotency_key.side_effect = lambda *a: tasks[-1] if tasks else None
    svc._build_connection = MagicMock(return_value={'url': 'connection'})
    svc._exchange_caller_identity = AsyncMock()
    build.upgrade_async = AsyncMock(return_value={'publish_id': 77})
    baas.get_publish_progress.return_value = {'status': 'RUNNING'}
    return SimpleNamespace(service=svc, repo=instances, tasks=tasks, baas=baas,
                           build=build, bots=bots, bindings=bindings,
                           handler=AicodingCallerRestartHandler(lambda: svc))


async def request(w, force=True, **kwargs):
    return await w.service.get_caller_connection(**IDS, force_upgrade=force, **kwargs)


async def deliver(w):
    outcome = await w.handler._execute(w.tasks[-1].payload)
    if isinstance(outcome, (Complete, Fail)):
        w.tasks[-1].status = 'SUCCEEDED' if isinstance(outcome, Complete) else 'FAILED'
    return outcome


GUARD = 'agentclaw.community.core.bot_management.engines.aicoding.restart_backup.AicodingRestartBackupMixin.prepare_restart_async'


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
async def test_acceptance_returns_without_backup_single_init_before_enqueue(world, engine):
    world.bots.get_by_id_and_owner.return_value['active_engine'] = engine
    with patch(GUARD, new_callable=AsyncMock) as backup:
        result = await request(world, iam_token='never-persist-this')
    assert result['need_poll'] and result['connection'] is None
    backup.assert_not_awaited()
    world.build.upgrade_async.assert_not_awaited()
    assert len(world.tasks) == 1
    assert 'never-persist-this' not in str(world.tasks[0].payload)
    assert [w['status'] for w in world.repo.writes if w.get('status')] == ['init']


@pytest.mark.asyncio
async def test_repeated_force_and_poll_join_queued_operation(world):
    await request(world)
    for force in (True, False, True):
        assert (await request(world, force))['need_poll']
    assert len(world.tasks) == 1
    assert len(world.repo.writes) == 1


@pytest.mark.asyncio
async def test_worker_backup_then_upgrade_and_original_identity_poll(world):
    await request(world)
    seen = []

    async def backup(*args, **kwargs):
        seen.append('backup')
        # A concurrent polling request must not use the old successful publish.
        assert (await request(world, False))['need_poll']
        assert len(world.repo.writes) == 2  # HTTP init + EXECUTING, not another init

    async def upgrade(**kwargs):
        seen.append('upgrade')
        assert world.repo.row['ext'][KEY]['phase'] == 'SUBMITTING'
        return {'publish_id': 77}

    world.build.upgrade_async.side_effect = upgrade
    with patch(GUARD, side_effect=backup):
        assert isinstance(await deliver(world), Reschedule)
    assert seen == ['backup', 'upgrade']
    assert world.repo.row['ext']['baas_publish_id'] == 77
    assert [w['status'] for w in world.repo.writes if w.get('status')] == ['init']
    world.baas.get_publish_progress.return_value = {'status': 'SUCCESS'}
    assert isinstance(await deliver(world), Complete)
    assert world.repo.row['status'] == 'init'  # identity not yet exchanged
    result = await request(world, False, iam_token='request-token')
    assert not result['need_poll'] and result['connection'] == {'url': 'connection'}
    world.service._exchange_caller_identity.assert_awaited_once()
    assert world.repo.row['ext'][KEY]['phase'] == 'SUCCEEDED'
    world.build.upgrade_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_backup_failure_terminal_and_poll_never_retries(world):
    await request(world)
    with patch(GUARD, side_effect=RestartBackupError('backup_failed', 'unsafe raw detail')):
        assert isinstance(await deliver(world), Fail)
    assert world.repo.row['status'] == 'failed'
    assert '备份失败' in world.repo.row['ext']['error']['message']
    assert 'unsafe raw detail' not in str(world.repo.row)
    for _ in range(2):
        assert not (await request(world, False))['need_poll']
        assert isinstance(await deliver(world), Fail)
    world.build.upgrade_async.assert_not_awaited()
    assert len(world.tasks) == 1
    assert (await request(world))['need_poll']  # only an explicit request retries
    assert len(world.tasks) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['EXECUTING', 'SUBMITTING'])
async def test_redelivery_after_possible_submission_never_replays(world, phase):
    await request(world)
    world.repo.row['ext'][KEY]['phase'] = phase
    with patch(GUARD, new_callable=AsyncMock) as backup:
        assert isinstance(await deliver(world), Reschedule)
    backup.assert_not_awaited()
    world.build.upgrade_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_failure_persists_old_error_field(world):
    world.service._task_queue.enqueue.side_effect = RuntimeError('database unavailable')
    with pytest.raises(RuntimeError):
        await request(world)
    assert world.repo.row['status'] == 'failed'
    assert '提交失败' in world.repo.row['ext']['error']['message']
    assert not world.tasks


@pytest.mark.asyncio
async def test_init_write_failure_never_enqueues(world):
    world.repo.fail_next = True
    with pytest.raises(RuntimeError, match='state write failed'):
        await request(world)
    world.service._task_queue.enqueue.assert_not_called()
    assert world.repo.row['status'] == 'failed'


@pytest.mark.asyncio
async def test_timeout_surfaces_without_frontend_and_does_not_upgrade(world):
    await request(world)
    world.tasks[0].payload['started_at'] -= 2101
    assert isinstance(await deliver(world), Fail)
    assert world.repo.row['status'] == 'failed'
    assert '超时' in world.repo.row['ext']['error']['message']
    world.build.upgrade_async.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['FAILED', 'REJECTED', 'REVOKED'])
async def test_background_deployment_failure_is_terminal(world, status):
    await request(world)
    with patch(GUARD, new_callable=AsyncMock):
        await deliver(world)
    world.baas.get_publish_progress.return_value = {'status': status}
    assert isinstance(await deliver(world), Fail)
    assert not (await request(world, False))['need_poll']
    world.build.upgrade_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_identity_failure_does_not_trigger_second_upgrade(world):
    await request(world)
    with patch(GUARD, new_callable=AsyncMock):
        await deliver(world)
    world.baas.get_publish_progress.return_value = {'status': 'SUCCESS'}
    world.service._exchange_caller_identity.side_effect = RuntimeError('identity failed')
    with pytest.raises(RuntimeError, match='identity failed'):
        await request(world, False, iam_token='request-token')
    assert world.repo.row['status'] == 'failed'
    assert not (await request(world, False))['need_poll']
    world.build.upgrade_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_other_engine_keeps_inline_upgrade_and_no_task(world):
    world.bots.get_by_id_and_owner.return_value['active_engine'] = 'openclaw'
    with patch(GUARD, new_callable=AsyncMock) as backup:
        await request(world)
    world.build.upgrade_async.assert_awaited_once()
    backup.assert_not_awaited()  # other engines never enter the coding strategy
    assert not world.tasks
    assert KEY not in world.repo.row['ext']


@pytest.mark.asyncio
async def test_first_creation_and_healthy_reuse_unchanged(world):
    result = await request(world, False)
    assert result['connection'] == {'url': 'connection'}
    assert not world.tasks
    world.repo.row['ext'].pop('bot_uuid')
    world.service._create_container = AsyncMock(return_value={'bot_uuid': BOT_UUID, 'publish_id': 11})
    await request(world)
    world.service._create_container.assert_awaited_once()
    assert not world.tasks


@pytest.mark.asyncio
async def test_changed_publish_or_engine_fails_before_backup(world):
    await request(world)
    world.bots.get_by_id_and_owner.return_value['active_engine'] = 'openclaw'
    with patch(GUARD, new_callable=AsyncMock) as backup:
        assert isinstance(await deliver(world), Fail)
    backup.assert_not_awaited()
    world.build.upgrade_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_lost_lock_before_submit_keeps_old_container(world):
    await request(world)

    async def backup(*args, **kwargs):
        world.service._restart_locks.row = SimpleNamespace(lock_token='new-owner')

    with patch(GUARD, side_effect=backup):
        assert isinstance(await deliver(world), Complete)
    world.build.upgrade_async.assert_not_awaited()
    assert world.service._restart_locks.row.lock_token == 'new-owner'


@pytest.mark.asyncio
async def test_lifecycle_registers_wakeup(world):
    registry = MagicMock()
    await AicodingCallerRestartLifecycle(registry, world.handler).bootstrap()
    registry.register.assert_called_once_with(world.handler, wake_on_enqueue=True)


def test_key_is_per_caller_and_unambiguous():
    assert _key(IDS) != _key({**IDS, 'user_id': 'another-caller'})
    assert _key({**IDS, 'user_id': 'ab', 'bot_id': 'c'}) != _key({**IDS, 'user_id': 'a', 'bot_id': 'bc'})


@pytest.mark.asyncio
async def test_two_concurrent_requests_use_real_sqlite_lock_and_single_init(world, tmp_path):
    from sqlalchemy import create_engine
    from agentclaw.community.core.repository.implementations.chat.expert_chat_instance import ExpertChatInstanceRepository
    from agentclaw.community.core.repository.implementations.bot.restart_lock import BotRestartLockRepository
    from agentclaw.community.core.bot_management.repository.models import BotRestartLockModel
    from tests.community.repository.chat.test_expert_chat_instance_unified import _create_schema, _FileSqliteDB

    engine = create_engine(f'sqlite:///{tmp_path / "caller.db"}', connect_args={'check_same_thread': False})
    _create_schema(engine)
    BotRestartLockModel.__table__.create(engine)
    db = _FileSqliteDB(engine)
    repo = ExpertChatInstanceRepository(db)
    repo.upsert_instance(**IDS, status='success', ext=world.repo.row['ext'])
    world.service._instance_repo = repo
    world.service._restart_locks = BotRestartLockRepository(db)
    original_update = repo.update_instance
    writes = []

    def update(**kwargs):
        writes.append(kwargs.get('status'))
        return original_update(**kwargs)

    repo.update_instance = update

    def enqueue(task_type, payload, **kwargs):
        assert repo.get_instance(**IDS)['status'] == 'init'
        task = SimpleNamespace(status='PENDING', payload=payload)
        world.tasks.append(task)
        return task, True

    world.service._task_queue.enqueue.side_effect = enqueue
    await asyncio.gather(*(asyncio.to_thread(lambda: asyncio.run(request(world))) for _ in range(2)))
    assert len(world.tasks) == 1
    assert writes == ['init']
    state = CallerRestartState(world.service, IDS)
    assert world.service._restart_locks.get(**state.lock_args) is None
    engine.dispose()


@pytest.mark.asyncio
async def test_real_handle_restores_tenant_scope(world):
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope, get_current_avernet_tenant
    with avernet_tenant_scope('caller-tenant'):
        await request(world)
    seen = []

    async def backup(*args, **kwargs):
        seen.append(get_current_avernet_tenant())

    with patch(GUARD, side_effect=backup):
        assert isinstance(await asyncio.to_thread(world.handler.handle, world.tasks[0].payload), Reschedule)
    assert seen == ['caller-tenant']


@pytest.mark.asyncio
async def test_backup_and_submission_failure_are_terminal_without_second_init(world):
    await request(world)
    world.build.upgrade_async.side_effect = RuntimeError('secret-url-and-token')
    with patch(GUARD, new_callable=AsyncMock):
        assert isinstance(await deliver(world), Fail)
    assert world.repo.row['status'] == 'failed'
    assert world.repo.row['ext'][KEY]['phase'] == 'FAILED'
    assert 'secret-url-and-token' not in str(world.repo.row)
    assert not (await request(world, False))['need_poll']
    assert [w['status'] for w in world.repo.writes if w.get('status')] == ['init', 'failed']


@pytest.mark.asyncio
async def test_immediate_worker_delivery_cannot_write_init_again(world):
    enqueue = world.service._task_queue.enqueue.side_effect
    seen = []

    def immediate(*args, **kwargs):
        from concurrent.futures import ThreadPoolExecutor
        result = enqueue(*args, **kwargs)
        # The real queue may wake a worker before enqueue has returned. It sees
        # the synchronous init, but waits for admission to release its lock.
        with ThreadPoolExecutor() as pool:
            seen.append(pool.submit(world.handler.handle, result[0].payload).result())
        return result

    world.service._task_queue.enqueue.side_effect = immediate
    assert (await request(world))['need_poll']
    assert isinstance(seen[0], Reschedule)
    assert [w['status'] for w in world.repo.writes if w.get('status')] == ['init']


@pytest.mark.asyncio
async def test_admission_clears_old_error_before_new_attempt(world):
    world.repo.row['ext']['error'] = {'message': 'previous failure'}
    await request(world)
    assert 'error' not in world.repo.row['ext']


def test_caller_lifecycle_and_service_resolve_in_real_di(test_injector):
    from agentclaw.community.core.expert_chat.services.expert_chat_instance_service import ExpertChatInstanceService
    lifecycle = test_injector.get(AicodingCallerRestartLifecycle)
    assert isinstance(lifecycle.handler, AicodingCallerRestartHandler)
    assert lifecycle.handler.service_provider() is test_injector.get(ExpertChatInstanceService)


@pytest.mark.asyncio
async def test_completed_deployment_exchanges_identity_for_pinned_publish(world):
    await request(world)
    original = world.service._publish_repo.get_by_id.return_value
    with patch(GUARD, new_callable=AsyncMock):
        await deliver(world)
    later = deepcopy(original)
    later.id = original.id + 1
    later.version = (original.version or 1) + 1
    world.service._publish_repo.get_by_publish_bot_id.return_value = later
    world.baas.get_publish_progress.return_value = {'status': 'SUCCESS'}
    result = await request(world, False, iam_token='request-token')
    assert not result['need_poll']
    assert world.service._exchange_caller_identity.await_args.kwargs['publish_id'] == original.id
    world.build.upgrade_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_backup_required_uses_original_upgrade(world):
    await request(world)
    with patch('agentclaw.community.core.bot_management.engines.aicoding.restart_backup.AicodingRestartBackupMixin.prepare_restart', return_value=None):
        assert isinstance(await deliver(world), Reschedule)
    world.build.upgrade_async.assert_awaited_once()
    assert world.repo.row['ext'][KEY]['phase'] == 'WAITING_READY'


@pytest.mark.asyncio
async def test_deadline_elapsed_during_backup_prevents_submission(world):
    await request(world)

    async def backup(*args, **kwargs):
        world.tasks[0].payload['started_at'] -= 2101

    with patch(GUARD, side_effect=backup):
        assert isinstance(await deliver(world), Fail)
    world.build.upgrade_async.assert_not_awaited()
    assert world.repo.row['status'] == 'failed'


@pytest.mark.asyncio
async def test_backup_budget_context_restored_after_failure(world):
    from agentclaw.community.core.bot_management.engines.aicoding.restart_backup import caller_backup_deadline
    await request(world)
    seen = []

    async def backup(*args, **kwargs):
        seen.append(caller_backup_deadline.get())
        raise RestartBackupError('timeout', 'deadline')

    with patch(GUARD, side_effect=backup):
        assert isinstance(await deliver(world), Fail)
    assert seen == [world.tasks[0].payload['started_at'] + 1500]
    assert caller_backup_deadline.get() is None
    assert '超时' in world.repo.row['ext']['error']['message']


@pytest.mark.asyncio
async def test_transient_progress_read_retries_observation_not_upgrade(world):
    await request(world)
    with patch(GUARD, new_callable=AsyncMock):
        await deliver(world)
    world.baas.get_publish_progress.side_effect = TimeoutError('temporary')
    with pytest.raises(TimeoutError):
        await deliver(world)
    assert world.repo.row['ext'][KEY]['phase'] == 'WAITING_READY'
    assert world.repo.row['status'] == 'init'
    world.baas.get_publish_progress.side_effect = None
    assert isinstance(await deliver(world), Reschedule)
    world.build.upgrade_async.assert_awaited_once()
