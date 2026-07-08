"""Rule 25 conformance — DeviceConnectionManagerPlugin.

Consumer under test: ``POST /api/approvals/mode/get`` (api/approvals/router.py).
It Depends on ``get_connection_manager`` which returns the plugin,
then calls ``manager.get_client(...)`` and branches on whether the
result is truthy. The local ``NoopDeviceConnectionManagerPlugin``
returns ``None`` for every method.

Plugin-hit assertion: the endpoint emits a specific error string
("No device connection for entity: ...") that is only producible if
the consumer observed ``None`` back from the plugin's ``get_client``.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_approval_mode_get_short_circuits_when_plugin_returns_none(
    app_with_testing_modules,
) -> None:
    client = TestClient(app_with_testing_modules)
    resp = client.post(
        "/api/approvals/mode/get",
        cookies={"staff_id": "alice"},
        json={"user_id": "alice", "session_key": "s1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is False
    # Error string is uniquely produced when manager.get_client returns
    # falsy — proves the consumer routed through the plugin.
    assert "No device connection" in body["error"]
