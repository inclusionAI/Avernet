import pytest
from fastapi.testclient import TestClient
from agentclaw.community.core.devices.services.device_context import DeviceOfflineError
from agentclaw.community.core.devices.services.device_context_resolver import DeviceContextResolver
from agentclaw.community.core.services.resource_file_service import ResourceFileService
from tests.community.endpoints.test_channel_router import _seed_owner_with_bot
from tests.community.endpoints.test_resource_file_router_local import _seed_local_bot, _BOT, _OWNER
from tests.community.framework import bind_failing_method

def test_channel_offline(app_with_testing_modules, world):
    _seed_owner_with_bot(world)
    bind_failing_method(world, DeviceContextResolver, 'resolve_for_bot', DeviceOfflineError('simulated inactive runtime'))
    with TestClient(app_with_testing_modules, raise_server_exceptions=False) as client:
        r = client.get('/api/channels/openclaw-configs', params={'bot_id':'bot_test','owner_id':'u_owner'}, headers={'x-user-id':'u_owner'})
    assert r.status_code == 503

@pytest.mark.parametrize('action', ['download', 'preview'])
def test_file_offline(app_with_testing_modules, world, action):
    _seed_local_bot(world)
    bind_failing_method(world, ResourceFileService, 'read_file', DeviceOfflineError('simulated inactive runtime'))
    with TestClient(app_with_testing_modules, raise_server_exceptions=False) as client:
        r = client.get('/api/resources/files/'+action, params={'path':'data/report.csv','bot_id':_BOT,'owner_id':_OWNER}, headers={'x-user-id':_OWNER})
    assert r.status_code == 503
