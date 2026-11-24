from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
from agentclaw.community.core.devices.services.device_context import DeviceConnectionUnavailableError, DeviceOfflineError
from agentclaw.community.core.devices.services.device_filesystem import checked_read_file
from agentclaw.community.core.devices.services.local_device_filesystem import LocalDeviceFileSystem
from agentclaw.community.core.devices.services.teclaw_device_filesystem import TeclawDeviceFileSystem
from agentclaw.community.core.service_bot.baas_service_errors import BaasNoActiveDevicesError


@pytest.mark.asyncio
@pytest.mark.parametrize('cls', [BaasDeviceFileSystem, TeclawDeviceFileSystem])
async def test_checked_read_distinguishes_offline_from_missing(cls):
    transport = MagicMock()
    fs = cls(transport=transport, conn_info={}, path_mapper=lambda p: p)
    transport.post.side_effect = BaasNoActiveDevicesError(status_code=404)
    with pytest.raises(DeviceOfflineError):
        await fs.read_file_checked('file')
    transport.post.side_effect = httpx.ConnectError('unreachable')
    with pytest.raises(DeviceConnectionUnavailableError):
        await fs.read_file_checked('file')
    transport.post.side_effect = None
    transport.post.return_value = httpx.Response(404, request=httpx.Request('POST', 'https://example.test'))
    assert await fs.read_file_checked('file') is None


@pytest.mark.asyncio
async def test_checked_read_legacy_provider_fails_without_assuming_absence():
    class Legacy:
        async def read_file(self, path, *, enforce_download_limit=False):
            return None
    with pytest.raises(RuntimeError, match='cannot distinguish'):
        await checked_read_file(Legacy(), 'file')


@pytest.mark.asyncio
async def test_checked_read_legacy_provider_keeps_successful_reads():
    class Legacy:
        async def read_file(self, path, *, enforce_download_limit=False):
            return b'legacy'
    assert await checked_read_file(Legacy(), 'file') == b'legacy'


@pytest.mark.asyncio
async def test_checked_download_does_not_call_legacy_provider():
    class Legacy:
        async def read_file(self, path, *, enforce_download_limit=False):
            pytest.fail('legacy download cannot prove size precheck')
    with pytest.raises(RuntimeError, match='checked downloads'):
        await checked_read_file(Legacy(), 'file', enforce_download_limit=True)


@pytest.mark.asyncio
async def test_local_checked_read_preserves_absence_and_content(tmp_path):
    fs = LocalDeviceFileSystem()
    f = tmp_path / 'file'
    assert await fs.read_file_checked(str(f)) is None
    f.write_bytes(b'ok')
    assert await fs.read_file_checked(str(f)) == b'ok'


@pytest.mark.asyncio
async def test_teclaw_checked_read_does_not_turn_denial_into_unavailability():
    transport = MagicMock()
    transport.post.return_value = httpx.Response(403, request=httpx.Request('POST', 'https://example.test'))
    fs = TeclawDeviceFileSystem(transport=transport, conn_info={}, path_mapper=lambda p: p)
    assert await fs.read_file('file') is None
    with pytest.raises(httpx.HTTPStatusError) as error:
        await fs.read_file_checked('file')
    assert error.value.response.status_code == 403


def test_publish_context_propagates_offline_only_for_checked_read():
    from agentclaw.community.core.services.resource_file_service import ResourceFileService

    service = ResourceFileService.__new__(ResourceFileService)
    service._stage_bind_id = MagicMock(return_value=12)
    service._resolver = MagicMock()
    service._resolver.resolve_for_binding.side_effect = DeviceOfflineError('offline')
    with pytest.raises(DeviceOfflineError):
        service._resolve_ctx(bot_id='b', entity_id='u', publish_id='p', checked=True)
    with pytest.raises(ValueError):
        service._resolve_ctx(bot_id='b', entity_id='u', publish_id='p')


@pytest.mark.asyncio
@pytest.mark.parametrize('code, expected', [('NO_DEVICES_FOUND', DeviceOfflineError), ('BOT_NOT_FOUND', httpx.HTTPStatusError)])
async def test_desktop_proxy_classifies_only_explicit_missing_device(code, expected):
    from agentclaw.community.core.devices.services.baas_invoke_transport import DesktopBaasInvokeTransport

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404, json={'detail': {'error': code}}, request=request)))
    transport = DesktopBaasInvokeTransport(baas_base_url='http://baas.test', tenant='test', bot_uuid='bot', engine_port=1, headers={}, client=client)
    fs = BaasDeviceFileSystem(transport=transport, conn_info={}, path_mapper=lambda path: path)
    with pytest.raises(expected):
        await fs.read_file_checked('file')
    client.close()


@pytest.mark.asyncio
async def test_checked_local_read_rejects_fifo_without_opening(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo)
    def forbidden_read(_self):
        pytest.fail('must not open a FIFO')
    monkeypatch.setattr(Path, 'read_bytes', forbidden_read)
    assert await LocalDeviceFileSystem().read_file_checked(str(fifo)) is None


@pytest.mark.asyncio
async def test_checked_local_read_rechecks_open_file_after_replacement(tmp_path, monkeypatch):
    import os
    from agentclaw.community.core.devices.services import local_device_filesystem

    regular = tmp_path / 'regular'
    regular.write_bytes(b'ok')
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo)
    real_open = os.open
    monkeypatch.setattr(local_device_filesystem.os, 'open', lambda _path, flags: real_open(fifo, flags))
    assert await LocalDeviceFileSystem().read_file_checked(str(regular)) is None


@pytest.mark.asyncio
async def test_checked_local_read_propagates_permission_error(tmp_path, monkeypatch):
    from agentclaw.community.core.devices.services import local_device_filesystem
    def denied(_path):
        raise PermissionError('denied')
    monkeypatch.setattr(local_device_filesystem.os, 'stat', denied)
    with pytest.raises(PermissionError):
        await LocalDeviceFileSystem().read_file_checked(str(tmp_path / 'x'))
