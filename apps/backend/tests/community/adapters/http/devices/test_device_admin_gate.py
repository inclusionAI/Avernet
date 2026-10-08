"""Deny-path coverage for the config-driven device_admin gate.

The two admin-only device endpoints authorize via
``core.access.admin_scopes.device_admin()``. Under the test profile the
``device_admin`` scope is seeded with ``"100000"``, so a caller whose staffId
is not in that set must be denied. The deny branch returns before touching the
injected service, so a ``None`` service is sufficient.
"""
import asyncio

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.devices import router as devices_router
from agentclaw.community.adapters.http.devices.schemas import ExecShellRequest


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _non_admin() -> AuthenticatedUser:
    # staffId not in the test overlay's device_admin allow-list (["100000"]).
    return AuthenticatedUser(id="u", staffId="999999", operatorName="op")


def test_list_connectable_admin_denies_non_device_admin():
    resp = _run(
        devices_router.list_connectable_devices_admin(user=_non_admin(), service=None)
    )
    assert resp.success is False
    assert "无权限" in resp.message


def test_exec_shell_denies_non_device_admin():
    req = ExecShellRequest(client_ids=[], shell_cmd="echo hi")
    resp = _run(devices_router.exec_shell(req=req, user=_non_admin(), service=None))
    assert resp.success is False
    assert "无权限" in resp.message
