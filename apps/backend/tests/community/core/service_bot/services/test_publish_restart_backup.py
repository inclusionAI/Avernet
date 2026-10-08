"""Mandatory published-instance restart precondition integration."""
from unittest.mock import Mock, AsyncMock, patch

import pytest

from tests.community.core.service_bot.services.test_publish_crash_windows import (
    FakeBaas, _ledger, _record, _restart_flow, _crash_before_record, PublishStatus,
)


@pytest.mark.asyncio
async def test_restart_backup_failure_never_issues_or_releases_published_target():

    ledger = _ledger()
    baas = FakeBaas()
    build = Mock()
    record = _record(PublishStatus.SUCCESS.value)
    record.ext['binding'] = {'online': 42}
    svc = _restart_flow(ledger, baas, build, record)
    svc._release_binding = Mock()
    targets = []

    async def guard(*, bot, device_id, target_runtime, restart_key=None):
        targets.append((device_id, restart_key))
        raise RuntimeError('backup failed')

    with patch('agentclaw.community.core.service_bot.services.publish_flow.restart_mixin.prepare_instance_restart', side_effect=guard), pytest.raises(RuntimeError, match='backup failed'):
        await svc.execute_restart(1, 'online', 'operator')
    assert targets == [('BOT-live', 'restart:1:online')]
    build.upgrade_async.assert_not_called()
    build.retire_superseded_bot.assert_not_called()
    build.release_async.assert_not_called()
    svc._release_binding.assert_not_called()


@pytest.mark.asyncio
async def test_restart_adopted_workflow_does_not_backup_replacement_again():
    ledger = _ledger()
    baas = FakeBaas()
    build = Mock()

    async def upgrade_async(**kw):
        return {'publish_id': baas.issue('BOT-live', 'UPDATE'), 'bot_uuid': 'BOT-live'}

    build.upgrade_async = upgrade_async
    record = _record(PublishStatus.SUCCESS.value)
    record.ext['binding'] = {'online': 42}
    svc = _restart_flow(ledger, baas, build, record)
    with patch('agentclaw.community.core.service_bot.services.publish_flow.restart_mixin.prepare_instance_restart', new_callable=AsyncMock) as guard:
        _crash_before_record(ledger)
        with pytest.raises(RuntimeError):
            await svc.execute_restart(1, 'online', 'operator')
        guard.assert_called_once()
        await svc.execute_restart(1, 'online', 'operator')
        guard.assert_called_once()
