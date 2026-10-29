"""Native restart delivery/ledger stay retryable across repeated failures."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.repository.implementations.publishing.publish_operation import (
    OrmPublishOperationRepository,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.services.publish_flow.operation_runner import (
    PublishOperationRunner,
)
from tests.community.repository.publishing.test_knowledge_restart_retry import knowledge_state
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    _real_flow, _start_online, publication as publication_fixture,
)
from tests.community.repository.publishing.test_knowledge_short_transaction import finish

publication = publication_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['direct_recreate', 'bot_gone_recreate', 'upgrade'])
async def test_real_restart_delivery_and_repeat_retry_preserve_selection(
    publication, mode, monkeypatch,
):
    db, repo, draft = publication
    live = finish(repo, _start_online(repo, draft))
    repo.compare_and_set_ext(
        publish_id=live.id, expected_ext=live.ext,
        ext={**live.ext, 'binding': {'online': 11},
             'migration_path': '/external-artifact', 'sbot_pin_image': False},
    )
    flow, _queue = _real_flow(repo)
    ledger = OrmPublishOperationRepository(db)
    flow._publish_operation_repo = ledger
    flow._operation_runner = PublishOperationRunner(
        ledger=ledger, baas_service=flow._baas_service,
    )
    flow._bot_service.get_bot_by_address.return_value = {
        'bot_id': 'service', 'id': 1, 'entity_id': 'owner',
        'entity_type': 'staff', 'owner_id': 'owner', 'bot_type': 'service',
    }
    flow._device_binding_repo.get_by_id.return_value = SimpleNamespace(
        device_id='device', device_provider='baas',
    )
    flow._device_binding_repo.insert_binding.return_value = 12
    flow._baas_service.resolve_container_provider.return_value = 'baas'
    flow._baas_service.get_bot.return_value = {
        'status': 'RELEASED' if mode == 'direct_recreate' else 'ACTIVE',
    }
    flow._baas_service.list_bot_publishes.return_value = []
    flow.resolve_publish_image_pin = lambda *a, **k: SimpleNamespace(docker_image=None)
    monkeypatch.setattr(
        'agentclaw.community.core.service_bot.services.publish_flow.restart_mixin.prepare_instance_restart',
        AsyncMock(),
    )
    flow._build_service.release_async = AsyncMock(side_effect=[
        {'publish_id': 300 + i, 'bot_uuid': f'new-device-{i}'} for i in range(4)
    ])
    flow._build_service.upgrade_async = (
        AsyncMock(side_effect=[{'publish_id': 300 + i} for i in range(4)])
        if mode == 'upgrade' else
        AsyncMock(return_value={'success': False, 'error_code': 'BOT_NOT_FOUND'})
    )
    flow.get_baas_publish_progress = MagicMock(return_value={
        'status': 'FAILED', 'failed_devices': ['device'],
    })
    before = knowledge_state(db)
    submitted = await flow.execute_restart(live.id, 'online', 'owner')
    assert submitted['success']
    # execute_restart and its operation runner are deliberately not replaced.
    for _ in range(2):
        assert flow.sync_restart_progress(live.id).status == PublishStatus.FAILED
        result = await flow.retry(live.id, operator='owner')
        assert result.action == 'restart'
        assert repo.get_by_id(live.id).status == PublishStatus.SUCCESS
        assert knowledge_state(db) == before
    assert len(ledger.list_by_publish_id(live.id)) >= 3
