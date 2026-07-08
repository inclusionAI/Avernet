"""Approvals router dependencies — DI factories."""
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.device_connection_manager import (
    DeviceConnectionManagerPlugin,
)


def get_connection_manager(
    manager: DeviceConnectionManagerPlugin = Injected(DeviceConnectionManagerPlugin),
) -> DeviceConnectionManagerPlugin:
    """FastAPI dependency: 返回 DeviceConnectionManager 单例。

    Resolves the ``DeviceConnectionManagerPlugin`` binding via the app
    injector through fastapi-injector's Depends-chain integration. The
    concrete impl lives in ``plugins/``; ``api/`` only names the
    ``plugin_api/`` protocol key.
    """
    return manager
