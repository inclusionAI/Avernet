"""A rejected restart is not a failed serving runtime."""
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.restart_backup import RestartBackupBlocked
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.task_queue.types import Fail
from tests.community.core.bot_management.aicoding.test_restart_task import (
    setup as setup, submit, task, mock_lifecycle,
)
from tests.community.core.service_bot.services.test_publish_restart_veto import published as published
from tests.community.core.bot_management.aicoding.test_restart_backup import response
from tests.community.core.bot_management.aicoding.test_restart_fastdisk import executor


@pytest.mark.asyncio
async def test_admission_keeps_old_runtime_active(setup):
    s = setup
    s.repo.bot['ext'].update(start_status='SUCCEEDED', start_message='ready')
    accepted = await submit(s)
    assert accepted['status'] == 'ACTIVE'
    assert accepted['binding_id'] == 7
    assert accepted['ext']['aicoding']['restart']['status'] == 'RUNNING'
    assert s.repo.bot['ext']['start_status'] == 'SUCCEEDED'
    assert s.repo.bot['ext']['unrelated'] == {'keep': True}
    s.service.restart_bot.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("template", [None, "applicationCoding"])
async def test_backup_veto_keeps_runtime_ready_and_reports_operation_failure(setup, template):
    s = setup
    s.repo.bot['ext'].update(start_status='SUCCEEDED', start_message='ready')
    s.repo.bot['template_type'] = template
    await submit(s)
    # An unrelated JSON writer racing the transition must survive the CAS retry.
    s.repo.conflict = True
    mock_lifecycle(s, backup_error=RestartBackupBlocked('backup_failed'))
    assert isinstance(s.handler.handle(task(s).payload), Fail)
    view = s.service.get_bot('b', 'o')
    assert view['status'] == 'ACTIVE'
    assert view['binding_id'] == 7
    assert s.binding['status'] == 'ACTIVE'
    assert view['ext']['start_status'] == 'SUCCEEDED'
    assert view['ext']['start_message'] == 'ready'
    assert view['ext']['unrelated'] == {'keep': True}
    assert view['ext']['concurrent'] == 1
    assert is_bot_ready(view)
    progress = s.strategy.get_restart_status(ext=view['ext'])
    assert progress['status'] == 'FAILED'
    assert progress['phase'] == 'BACKING_UP'
    assert '快盘' in progress['message']
    assert isinstance(s.handler.handle(task(s).payload), Fail)
    s.service.restart_bot.assert_called_once()


@pytest.mark.asyncio
async def test_published_veto_preserves_success_and_binding(published):
    from agentclaw.community.core.bot_management.engines.aicoding.restart_backup import _operation_id
    svc, _, baas, build, record = published
    operation = _operation_id(None, 'restart:1:online')
    execute = executor([response('failed', operation_id=operation)])
    baas.post_bots_api = Mock(side_effect=lambda **kw: execute(kw['payload']['cmd']))
    result = await svc.execute_restart(1, 'online', 'operator')
    assert result['success'] is False
    assert record.status == 'success'
    assert record.ext['binding'] == {'online': 42}
    assert 'error_message' not in record.ext
    assert record.ext['restart']['status'] == 'FAILED'
    progress = svc.sync_restart_progress(1)
    assert progress.status == 'success'
    assert progress.ext['aicoding']['restart']['status'] == 'FAILED'
    assert '快盘' in progress.ext['aicoding']['restart']['message']
    build.upgrade_async.assert_not_called()


@pytest.mark.asyncio
async def test_openapi_and_internal_status_agree_after_veto(setup):
    import inspect
    from types import SimpleNamespace
    from starlette.requests import Request
    from agentclaw.community.adapters.http.bot_management.router import get_bot_status as internal
    from agentclaw.community.adapters.http.openapi_v1.bots.router import get_bot_status as public

    s = setup
    await submit(s)
    mock_lifecycle(s, backup_error=RestartBackupBlocked('backup_failed'))
    s.handler.handle(task(s).payload)
    internal_result = await inspect.unwrap(internal)(
        bot_id='b', owner_id='o', ctx=SimpleNamespace(user_id='o'), bot_service=s.service,
    )
    public_result = await inspect.unwrap(public)(
        bot_id='b', owner_id='o', user_id='o', bot_service=s.service,
        request=Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': []}),
    )
    assert internal_result.data['bot_status'] == public_result.data.status == 'ACTIVE'
    assert internal_result.data['is_ready'] is public_result.data.is_ready is True
    assert internal_result.data['ext']['aicoding']['restart'] == public_result.data.ext['aicoding']['restart']
    assert internal_result.data['error_message'] is None
    assert 'restart' not in internal_result.data
    assert 'restart' not in public_result.data.model_dump()
    assert 'coding_restart' not in public_result.data.ext


@pytest.mark.asyncio
async def test_explicit_new_published_attempt_does_not_reuse_failed_receipt(published):
    from unittest.mock import AsyncMock
    from agentclaw.community.core.bot_management.engines.registry import resolve_published_status_ext
    from tests.community.core.bot_management.aicoding.test_restart_backup import partial_response

    svc, _, baas, build, record = published
    record.ext.setdefault('restart', {})['custom'] = {'keep': [1, 2]}
    first = svc.restart_bot(1, 'operator')['ext']['aicoding']['restart']['operation_id']
    assert record.ext['unrelated'] == {'keep': True}
    assert record.ext['restart']['custom'] == {'keep': [1, 2]}
    execute = executor([response('failed', operation_id=first)])
    baas.post_bots_api = Mock(side_effect=lambda **kw: execute(kw['payload']['cmd']))
    assert not (await svc.execute_restart(1, 'online', 'operator', operation_id=first))['success']
    # Redelivery is terminal and does not rerun detection or container mutation.
    count = baas.post_bots_api.call_count
    assert not (await svc.execute_restart(1, 'online', 'operator', operation_id=first))['success']
    assert baas.post_bots_api.call_count == count
    second = svc.restart_bot(1, 'operator')['ext']['aicoding']['restart']['operation_id']
    assert second != first
    assert record.ext['restart']['phase'] == 'QUEUED'
    assert record.ext['unrelated'] == {'keep': True}
    assert record.ext['restart']['custom'] == {'keep': [1, 2]}
    assert svc.sync_restart_progress(1).ext['aicoding']['restart']['status'] == 'RUNNING'
    execute = executor([partial_response(operation_id=second)] * 3)
    baas.post_bots_api.side_effect = lambda **kw: execute(kw['payload']['cmd'])
    build.upgrade_async = AsyncMock(return_value={'success': True, 'publish_id': 902})
    assert (await svc.execute_restart(1, 'online', 'operator', operation_id=second))['success']
    assert record.status == 'success'
    assert record.ext['unrelated'] == {'keep': True}
    assert record.ext['restart']['custom'] == {'keep': [1, 2]}
    assert resolve_published_status_ext(record, progress={'status': 'SUCCESS'})['aicoding']['restart']['status'] == 'SUCCEEDED'


@pytest.mark.parametrize('engine', ['openclaw', 'hermes', 'moltis', 'teclaw', None])
def test_default_engines_and_legacy_status_json_are_unchanged(engine):
    from agentclaw.community.core.bot_management.engines.registry import resolve_status_ext
    from agentclaw.community.adapters.http.openapi_v1.bots.schemas import BotStatus
    assert resolve_status_ext({'active_engine': engine, 'ext': {
        'coding_restart': {'operation_id': 'foreign', 'phase': 'FAILED'},
    }}, response_ext={}) == {}
    assert BotStatus(status='ACTIVE', is_ready=True, device_id='old').model_dump() == {
        'status': 'ACTIVE', 'is_ready': True, 'device_id': 'old',
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_status', ['SUCCESS', 'FAILED'])
async def test_published_workflow_terminal_keeps_original_lifecycle_and_operation_result(published, provider_status):
    from unittest.mock import AsyncMock
    from tests.community.core.bot_management.aicoding.test_restart_backup import partial_response

    svc, _, baas, build, record = published
    operation = svc.restart_bot(1, 'operator')['ext']['aicoding']['restart']['operation_id']
    execute = executor([partial_response(operation_id=operation)] * 3)
    baas.post_bots_api = Mock(side_effect=lambda **kw: execute(kw['payload']['cmd']))
    build.upgrade_async = AsyncMock(return_value={'success': True, 'publish_id': 902})
    assert (await svc.execute_restart(1, 'online', 'operator', operation_id=operation))['success']
    svc.get_baas_publish_progress = Mock(return_value={'status': provider_status})
    svc._activate_binding = Mock()
    svc._refresh_provider_mcp_after_success = Mock()
    svc.refresh_publish_handle = Mock()
    # Repository reads return detached snapshots, not the mutable test store.
    svc.get_publish_record = Mock(side_effect=lambda _: record.model_copy(deep=True))
    result = svc.sync_restart_progress(1)
    assert result.ext['aicoding']['restart']['operation_id'] == operation
    assert result.ext['aicoding']['restart']['status'] == ('SUCCEEDED' if provider_status == 'SUCCESS' else 'FAILED')
    assert record.status == ('success' if provider_status == 'SUCCESS' else 'failed')
    assert record.ext['restart']['status'] == result.ext['aicoding']['restart']['status']
    if provider_status == 'FAILED':
        assert svc.sync_restart_progress(1).ext['aicoding']['restart']['status'] == 'FAILED'
    else:
        svc._activate_binding.assert_called_once()


@pytest.mark.asyncio
async def test_unhealthy_original_runtime_is_not_fabricated_as_active(setup):
    s = setup
    s.repo.bot.update(status='FAILED', template_type='applicationCoding')
    s.repo.bot['ext'].update(start_status='FAILED', start_message='old runtime failed')
    await submit(s)
    mock_lifecycle(s, backup_error=RestartBackupBlocked('backup_failed'))
    assert isinstance(s.handler.handle(task(s).payload), Fail)
    view = s.service.get_bot('b', 'o')
    assert view['status'] == 'FAILED'
    assert view['ext']['start_status'] == 'FAILED'
    assert view['ext']['start_message'] == 'old runtime failed'
    assert not is_bot_ready(view)
    assert s.strategy.get_restart_status(ext=view['ext'])['status'] == 'FAILED'


@pytest.mark.asyncio
async def test_openapi_restart_acceptance_exposes_the_operation_id(setup):
    import inspect
    from unittest.mock import AsyncMock
    from starlette.requests import Request
    from agentclaw.community.adapters.http.openapi_v1.bots.router import restart_bot

    s = setup
    async def submit_restart(**kwargs):
        return await submit(s)
    s.service.restart_bot_async = AsyncMock(side_effect=submit_restart)
    response = await inspect.unwrap(restart_bot)(
        bot_id='b', owner_id='o', user_id='o', bot_service=s.service,
        request=Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': []}),
    )
    assert response.data.status == 'ACTIVE'
    assert response.data.ext['aicoding']['restart']['status'] == 'RUNNING'
    assert response.data.ext['aicoding']['restart']['operation_id'] == task(s).payload['operation_id']
    assert response.model_dump()['data']['ext']['aicoding']['restart']['status'] == 'RUNNING'


@pytest.mark.asyncio
async def test_status_projection_merges_without_changing_persisted_ext(setup):
    import inspect
    from copy import deepcopy
    from types import SimpleNamespace
    from agentclaw.community.adapters.http.bot_management.router import get_bot_status

    s = setup
    s.repo.bot['ext'].update(aicoding={'other': {'keep': True}}, other_engine={'keep': 1})
    await submit(s)
    before = deepcopy(s.repo.bot['ext'])
    result = await inspect.unwrap(get_bot_status)(
        bot_id='b', owner_id='o', ctx=SimpleNamespace(user_id='o'), bot_service=s.service,
    )
    assert result.data['ext']['aicoding']['other'] == {'keep': True}
    assert result.data['ext']['aicoding']['restart']['status'] == 'RUNNING'
    assert result.data['ext']['other_engine'] == {'keep': 1}
    assert s.repo.bot['ext'] == before
