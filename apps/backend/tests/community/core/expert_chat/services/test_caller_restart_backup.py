"""Mandatory caller-container restart precondition integration."""
from unittest.mock import AsyncMock, patch

import pytest

from tests.community.core.expert_chat.services.test_expert_chat_instance_service import (
    _make_service, BOT_ID, OWNER_ID, BOT_UUID, ConnectionError,
)


@pytest.mark.asyncio
async def test_caller_upgrade_backup_failure_keeps_target_and_never_upgrades():

    svc, instances, _, _, bots, bindings, build, *_ = _make_service()
    bots.get_by_id_and_owner.return_value = {
        'bot_id': BOT_ID, 'owner_id': OWNER_ID, 'active_engine': 'aicoding'}
    seen = []

    async def guard(*, bot, device_id, target_runtime):
        seen.append(device_id)
        raise RuntimeError('backup failed')

    with patch('agentclaw.community.core.expert_chat.services.expert_chat_instance_service.prepare_instance_restart', side_effect=guard), pytest.raises(ConnectionError, match='backup failed'):
        await svc._upgrade_container(bot_uuid=BOT_UUID, bot_id=BOT_ID,
                                     owner_id=OWNER_ID, migration_path='/artifact')
    assert seen == [BOT_UUID]
    build.upgrade_async.assert_not_called()
    build.release_async.assert_not_called()
    bindings.update_status.assert_not_called()
    instances.update_instance.assert_not_called()


@pytest.mark.asyncio
async def test_caller_legacy_guard_returns_then_original_upgrade_runs():

    svc, _, _, _, bots, _, build, *_ = _make_service()
    bots.get_by_id_and_owner.return_value = {
        'bot_id': BOT_ID, 'owner_id': OWNER_ID, 'active_engine': 'claude_code'}
    order = []

    async def guard(*, bot, device_id, target_runtime):
        assert device_id == BOT_UUID
        order.append('legacy-allowed')

    async def upgrade(**kw):
        assert kw['bot_uuid'] == BOT_UUID
        order.append('upgrade')
        return {'publish_id': 77}

    build.upgrade_async = AsyncMock(side_effect=upgrade)
    with patch('agentclaw.community.core.expert_chat.services.expert_chat_instance_service.prepare_instance_restart', side_effect=guard):
        result = await svc._upgrade_container(bot_uuid=BOT_UUID, bot_id=BOT_ID,
                                             owner_id=OWNER_ID, migration_path='/artifact')
    assert result == {'bot_uuid': BOT_UUID, 'publish_id': 77}
    assert order == ['legacy-allowed', 'upgrade']


@pytest.mark.asyncio
async def test_real_coding_backup_transport_failure_still_upgrades_caller():
    from unittest.mock import Mock
    svc, _, _, _, bots, _, build, *_ = _make_service()
    bots.get_by_id_and_owner.return_value = {
        'bot_id': BOT_ID, 'owner_id': OWNER_ID, 'active_engine': 'claude_code',
    }
    svc._baas.list_devices_by_bot_uuid = Mock(return_value=[{'provider_device_id': 'physical', 'status': 'ACTIVE'}])
    svc._baas.post_bots_api = Mock(side_effect=RuntimeError('unreachable sandbox'))
    build.upgrade_async = AsyncMock(return_value={'publish_id': 77})
    result = await svc._upgrade_container(bot_uuid=BOT_UUID, bot_id=BOT_ID,
                                         owner_id=OWNER_ID, migration_path='/artifact')
    assert result == {'bot_uuid': BOT_UUID, 'publish_id': 77}
    assert svc._baas.post_bots_api.call_count == 2  # Detection fails, then legacy backup fails.
    build.upgrade_async.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['failed', 'partial_success', 'legacy'])
async def test_confirmed_fastdisk_caller_failure_keeps_old_container(outcome):
    from unittest.mock import Mock
    from agentclaw.community.core.bot_management.engines.aicoding.restart_backup import command
    from tests.community.core.bot_management.aicoding.test_restart_backup import response
    from tests.community.core.bot_management.aicoding.test_restart_fastdisk import detection, detection_command
    import re

    svc, instances, _, _, bots, bindings, build, *_ = _make_service()
    bots.get_by_id_and_owner.return_value = {
        'bot_id': BOT_ID, 'owner_id': OWNER_ID, 'active_engine': 'claude_code'}
    svc._baas.list_devices_by_bot_uuid = Mock(return_value=[{'provider_device_id': 'physical', 'status': 'ACTIVE'}])

    def execute(**kw):
        cmd = kw['payload']['cmd']
        if cmd == detection_command():
            return detection()
        operation = re.search(r'[a-f0-9]{32}', cmd).group()
        assert cmd == command('start', operation)
        return response(outcome, operation_id=operation)

    svc._baas.post_bots_api = Mock(side_effect=execute)
    with pytest.raises(ConnectionError, match='快盘'):
        await svc._upgrade_container(bot_uuid=BOT_UUID, bot_id=BOT_ID,
                                     owner_id=OWNER_ID, migration_path='/artifact')
    assert svc._baas.post_bots_api.call_count == 2
    build.upgrade_async.assert_not_called()
    build.release_async.assert_not_called()
    instances.update_instance.assert_not_called()
    bindings.update_status.assert_not_called()
