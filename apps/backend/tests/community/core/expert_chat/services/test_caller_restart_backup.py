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
