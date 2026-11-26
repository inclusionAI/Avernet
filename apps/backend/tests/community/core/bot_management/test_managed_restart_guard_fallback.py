from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_management.snapshot_guard import ManagedSnapshotConflictError, ManagedSnapshotPersistenceError


@pytest.mark.parametrize('failure', [ManagedSnapshotConflictError, ManagedSnapshotPersistenceError])
def test_unbound_restart_never_swallows_a_guarded_snapshot_write_refusal(failure):
    service = BotService.__new__(BotService)
    service.is_teclaw_bot = Mock(return_value=False)
    service._repository = Mock()
    service._repository.get_by_id_and_owner.return_value = {
        'bot_id': 'bot', 'owner_id': 'owner', 'entity_id': 'owner', 'entity_type': 'staff',
        'active_engine': 'aicoding', 'template_type': 'architect', 'bot_type': 'personal',
        'binding_id': None, 'status': 'FAILED', 'ext': {},
    }
    service._template_service = Mock()
    service._template_service.update_template_conditionally.side_effect = failure('write refused')
    service._device_service = Mock()
    with pytest.raises(failure):
        service.restart_bot('bot', 'owner', extra_configs={
            'template_config': {'template_key': 'coding', 'template_version_id': 4},
            'confirmed_template_update': 'true', 'expected_template_config_hash': 'a' * 64,
        })
    service._device_service.release_device.assert_not_called()
