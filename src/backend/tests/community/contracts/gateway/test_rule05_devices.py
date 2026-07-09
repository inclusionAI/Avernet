"""Rule #5 — 设备连接 (/api/v1/devices) 契约测试。

验证 GET /api/v1/devices/{id}/connection 和
GET /api/v1/devices/connectable 接口响应字段。

依赖:
- DeviceService DI mock
- conftest gw_client 提供认证 override
"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.devices.services.device_service import DeviceService

from tests.community.contracts.gateway.conftest import (
    assert_response_schema, assert_success, assert_has_fields,
    assert_response_data_contract, bind_mock_service,
)


def _bind_device_service(app):
    """Bind mock DeviceService and return it."""
    from agentclaw.community.core.devices.models import DeviceConnectionInfo

    svc = MagicMock(spec=DeviceService)

    # get_device_connection -> 返回 DeviceConnectionInfo
    # handler 经 connection_info_to_response 转为 DeviceConnectionResponse
    conn_info = DeviceConnectionInfo(
        type="remote",
        target="agentclawdevice-local.stable.teamclaw.net:20003",
        token="test-token-123",
        engine_type="openclaw",
        available=True,
        message="",
    )
    svc.get_device_connection.return_value = conn_info

    # list_connectable_devices -> 返回 (total, items)
    # items 是 list[DeviceBindingRecord]，handler 用 record_to_response 转为 dict
    mock_record = MagicMock()
    mock_record.id = 100
    mock_record.entity_id = "448524"
    mock_record.entity_type = "staff"
    mock_record.device_id = "device_001"
    mock_record.device_provider = "arca"
    mock_record.env = "pre"
    mock_record.device_props = {}
    mock_record.status = "ACTIVE"
    mock_record.last_alive_at = None
    svc.list_connectable_devices.return_value = (1, [mock_record])

    bind_mock_service(DeviceService, svc, app)
    return svc


class TestGetDeviceConnection:
    """GET /api/v1/devices/{id}/connection — 设备连接状态。"""

    def test_connection_structure(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        """验证返回的 DeviceConnectionResponse 包含文档规定字段。"""
        _bind_device_service(app_with_testing_modules)
        resp = gw_client.get("/api/v1/devices/100/connection")
        body = resp.json()

        assert_success(body, "GET devices/{id}/connection")
        assert_response_data_contract(body, "rule05_GET_api_v1_devices_id_connection", update=contract_snapshot_update)
        data = body["data"]
        assert_has_fields(
            data,
            {"type": str, "target": str, "token": str, "engine_type": str,
             "available": bool, "message": str},
            label="GET devices/{id}/connection data",
        )


class TestListConnectableDevices:
    """GET /api/v1/devices/connectable — 可连接设备列表。"""

    def test_connectable_structure(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        """验证返回包含 total/items 且 items 中有文档规定字段。"""
        _bind_device_service(app_with_testing_modules)
        resp = gw_client.get("/api/v1/devices/connectable", params={
            "entity_id": "448524", "entity_type": "staff",
        })
        body = resp.json()

        assert_success(body, "GET devices/connectable")
        assert_response_data_contract(body, "rule05_GET_api_v1_devices_connectable", update=contract_snapshot_update)
        data = body["data"]
        assert_has_fields(data, {"total": int, "items": list}, label="GET devices/connectable data")
        items = data["items"]
        assert_has_fields(items[0],
            {"id": int, "entity_id": str, "entity_type": str, "device_id": str, "device_provider": str},
            label="GET devices/connectable items[0]")
