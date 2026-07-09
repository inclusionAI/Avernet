"""DeviceConnectionManagerPlugin LOCAL+SQLITE wiring guard.

The ``world`` fixture (``tests/framework/fixtures.py``) builds a per-test
injector for the ``test`` profile (LOCAL stubs + SQLite, incl.
``TestingDevicesModule``). It is
re-exported here so this test — which lives outside ``tests/endpoints/`` —
can request it by name. Single box must resolve the Noop impl, never the prod
Moltis ``DeviceConnectionManager``.
"""
from agentclaw.community.plugin_api.device_connection_manager import DeviceConnectionManagerPlugin
from agentclaw.community.plugins.local.device_connection_manager import (
    NoopDeviceConnectionManagerPlugin,
)

# Re-export the framework fixtures into this module's collection scope so this
# test — outside ``tests/endpoints/`` — can request ``world`` by name. ``world``
# depends on ``app_with_testing_modules``, so both must be in scope (same
# pattern as ``tests/framework/conftest.py`` / ``tests/endpoints/conftest.py``).
from tests.community.framework.fixtures import (  # noqa: F401 — re-export for collection
    app_with_testing_modules,
    world,
)


def test_local_boot_binds_noop_device_connection_manager(world):
    plugin = world.get(DeviceConnectionManagerPlugin)
    assert isinstance(plugin, NoopDeviceConnectionManagerPlugin), (
        "single box must bind Noop, not prod Moltis DeviceConnectionManager"
    )
