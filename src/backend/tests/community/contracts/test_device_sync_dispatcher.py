"""Rule 25 conformance — DeviceSyncDispatcher.

Consumer under test: an upper-layer caller invokes
``DeviceSyncDispatcher.dispatch(ctx)`` and then calls the six Core
``DeviceSync`` methods on the returned service. The community local impl
``CommunityDeviceSyncDispatcher`` holds a DI-injected
``CommunityDeviceSyncService`` (Core) and returns it for any ``ctx`` — the
selection seam is exercised end-to-end (Protocol → impl → Core service).

Plugin-hit assertion: the returned object is a Core ``DeviceSync`` whose
no-op results match the community contract; dispatch returns the SAME injected
service instance for any ctx (selection only, no per-ctx construction).
"""
from __future__ import annotations

from types import SimpleNamespace

from agentclaw.community.core.devices.services.community_device_sync import (
    CommunityDeviceSyncService,
)
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.plugins.community.device_sync_dispatcher import (
    CommunityDeviceSyncDispatcher,
)


def test_dispatch_returns_core_device_sync_service() -> None:
    """``dispatch(ctx)`` returns the injected Core ``DeviceSync`` service
    (not a Plugin), and the six methods satisfy the community no-op contract."""
    service = CommunityDeviceSyncService()
    dispatcher: DeviceSyncDispatcher = CommunityDeviceSyncDispatcher(
        community_device_sync_service=service
    )

    ctx = SimpleNamespace(bot_id="b1", provider="arca")
    returned = dispatcher.dispatch(ctx)

    # Selection only: the returned service is the injected Core instance.
    assert returned is service
    assert isinstance(returned, DeviceSync)
    assert returned.sync_symlinks([{"source": "/s", "target": "/t"}])["success"] is False
    assert returned.sync_bot_config("b", 1, "1", "OWNER", "u", "n")["success"] is False
    assert returned.sync_all_mcp_servers([]) is True
    assert returned.sync_single_mcp({"server_code": "x"}) is True
    assert returned.sync_remove_mcp("x") is True
    assert returned.has_mcp("x") is True


def test_dispatch_is_selection_only_any_ctx() -> None:
    """The community dispatcher returns the same service for any ctx —
    no per-ctx construction, no provider branching."""
    service = CommunityDeviceSyncService()
    dispatcher = CommunityDeviceSyncDispatcher(
        community_device_sync_service=service
    )

    a = dispatcher.dispatch(SimpleNamespace(bot_id="b1", provider="baas"))
    b = dispatcher.dispatch(SimpleNamespace(bot_id="b2", provider="teclaw"))
    assert a is service
    assert b is service