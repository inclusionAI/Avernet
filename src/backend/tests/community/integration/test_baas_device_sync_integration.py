"""Integration tests for BAAS device filesystem via the legacy
``DeviceFilesystemDispatcher.for_bot`` compatibility layer.

Phase 2 Task 6 收口后 ``ProdDeviceSyncPluginSupplier`` 已删,对应的 sync 路径
integration test 整体移除(BAAS sync 走 ``DeviceSyncDispatcher.dispatch(ctx)``,
单测见 ``tests/plugins/prod/test_device_sync_dispatcher.py``)。

⚠️ 生产 DI 直绑 ArcaDeviceAccessor,业务路径走 ``DeviceContextResolver`` +
``dispatch(ctx)``。``DeviceFilesystemDispatcher.for_bot`` 兼容层是死路径,
仅在历史单测直接构造时使用,后续 spec 删完 20 个 fs.for_bot caller 后可清。
"""
from unittest.mock import MagicMock



def _fs_dispatcher(device_plugin=None, *, bot_repo=None, binding_repo=None,
                   baas_service=None, resolver_provider=None, **_ignored):
    """Test shim: core DeviceFilesystemDispatcher + injected prod resolver (B6 T26)."""
    from unittest.mock import MagicMock as _MM
    from agentclaw.community.core.devices.services.device_filesystem_dispatcher import (
        DeviceFilesystemDispatcher as _DFD,
    )
    from agentclaw.community.core.devices.services.device_filesystem_resolver import (
        DefaultDeviceFileSystemResolver as _Res,
    )
    resolve = _Res(
        baas_service=baas_service if baas_service is not None else _MM(),
        bot_repo=bot_repo if bot_repo is not None else _MM(),
        binding_repo=binding_repo if binding_repo is not None else _MM(),
        sandbox_client=_MM(),
    )
    return _DFD(
        device_plugin=device_plugin if device_plugin is not None else _MM(),
        resolve=resolve,
        resolver_provider=resolver_provider,
    )

class TestBaasDeviceFilesystemViaCompatLayer:
    """``DeviceFilesystemDispatcher.for_bot`` compat layer should route BAAS
    bots to BaasDeviceFileSystem."""

    def test_baas_bot_yields_baas_filesystem(self):
        """BAAS bot connection info should produce DesktopBaasDeviceFileSystem.

        2026-06-17 strategy 重构后:`(baas, desktop)` 是当前唯一合法的 baas 组合
        (其余 personal+baas / service+baas 在 _ILLEGAL_BOT_DEVICE_COMBINATIONS 中,
        待隐舟服务化重构后开放)。Desktop 子类继承自 BaasDeviceFileSystem。
        """

        mock_device = MagicMock()
        mock_device.get_connection_info.return_value = {
            "device_provider": "baas",
            "bot_type": "desktop",
            "bind_id": 42,
            "url": "https://baas-host/proxypass/baas-target",
            "token": "baas-tok",
            "headers": {"x-proxypass-token": "baas-tok"},
            "target": "baas-target",
            "sandbox_id": "baas-target",
            "use_proxy": True,
            "engine_type": "openclaw",
            "baas_base_url": "https://baas-host",
            "paas_device_id": 55,
            "engine_port": 20003,
        }

        dispatcher = _fs_dispatcher(
            device_plugin=mock_device,
            bot_repo=MagicMock(),
            binding_repo=MagicMock(),
            baas_service=MagicMock(),
        )
        fs = dispatcher.for_bot(bot_id="bot-1", user_id="u-1")

        from agentclaw.community.core.devices.services.baas_device_filesystem import (
            BaasDeviceFileSystem,
            DesktopBaasDeviceFileSystem,
        )
        assert isinstance(fs, BaasDeviceFileSystem)
        assert isinstance(fs, DesktopBaasDeviceFileSystem)
        # 2026-06-17 重构后 ctor 只留 _bot_uuid 用于日志识别(其它走 transport)
        assert fs._bot_uuid == 55


