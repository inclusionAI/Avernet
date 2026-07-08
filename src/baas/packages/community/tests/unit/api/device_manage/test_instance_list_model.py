# mypy: disable-error-code="call-arg"
"""Tests for PoolabInstanceSummary Pydantic model."""

from __future__ import annotations

from secbaas.api.device_manage._instance_list import PoolabInstanceSummary


class TestPoolabInstanceSummary:
    """Test cases for PoolabInstanceSummary Pydantic model."""

    def test_all_16_fields(self) -> None:
        """Should create PoolabInstanceSummary with all 16 fields matching instanceList API response."""
        data = {
            "id": "inst_mock_001",
            "instanceId": 100001,
            "hostName": "mock-host-name.example.com",
            "status": "OPENED",
            "type": "OpenClaw",
            "networkType": "PUBLIC_ONLY",
            "createdAt": "2023-01-01T00:00:00Z",
            "image": "https://via.placeholder.com/150",
            "operationsUrl": "http://mock-host.example.com:9999/?token=mock_token_value",
            "remoteUrl": "http://mock-host.example.com/vnc.html",
            "modelConfig": {"type": "PUBLIC"},
            "userId": "user_mock_001",
            "userNick": "测试用户",
            "env": "TEST",
            "tenantId": 100001,
            "passwdConfig": {"vncUser": "mock_user", "vncPasswd": "mock_password_123"},
        }

        summary = PoolabInstanceSummary(**data)

        # Verify by-alias construction produces correct Python field names
        assert summary.poolab_instance_list_id == "inst_mock_001"
        assert summary.instance_id == 100001
        assert summary.host_name == "mock-host-name.example.com"
        assert summary.status == "OPENED"
        assert summary.poolab_type == "OpenClaw"
        assert summary.network_type == "PUBLIC_ONLY"
        assert summary.created_at == "2023-01-01T00:00:00Z"
        assert summary.image == "https://via.placeholder.com/150"
        assert (
            summary.operations_url
            == "http://mock-host.example.com:9999/?token=mock_token_value"
        )
        assert summary.remote_url == "http://mock-host.example.com/vnc.html"
        assert summary.model_config_data == {"type": "PUBLIC"}
        assert summary.user_id == "user_mock_001"
        assert summary.user_nick == "测试用户"
        assert summary.env == "TEST"
        assert summary.tenant_id == 100001
        assert summary.passwd_config == {
            "vncUser": "mock_user",
            "vncPasswd": "mock_password_123",
        }

    def test_minimal_required_fields(self) -> None:
        """Should work with only id and instanceId (only required fields)."""
        summary = PoolabInstanceSummary(id="inst_min_001", instanceId=42)

        assert summary.poolab_instance_list_id == "inst_min_001"
        assert summary.instance_id == 42
        # All optional fields default to None
        assert summary.host_name is None
        assert summary.status is None
        assert summary.poolab_type is None
        assert summary.network_type is None
        assert summary.created_at is None
        assert summary.image is None
        assert summary.operations_url is None
        assert summary.remote_url is None
        assert summary.model_config_data is None
        assert summary.user_id is None
        assert summary.user_nick is None
        assert summary.env is None
        assert summary.tenant_id is None
        assert summary.passwd_config is None

    def test_optional_fields_default_none(self) -> None:
        """Optional fields should default to None when absent."""
        summary = PoolabInstanceSummary(id="inst_001", instanceId=1)

        assert summary.model_config_data is None
        assert summary.passwd_config is None
        assert summary.host_name is None
        assert summary.operations_url is None
        assert summary.remote_url is None
        assert summary.user_nick is None

    def test_populate_by_name_allows_python_field_names(self) -> None:
        """model_config populate_by_name should allow construction with Python field names."""
        summary = PoolabInstanceSummary(
            poolab_instance_list_id="inst_py_001",
            instance_id=99,
            poolab_type="OpenClaw",
            network_type="PRIVATE",
        )

        assert summary.poolab_instance_list_id == "inst_py_001"
        assert summary.instance_id == 99
        assert summary.poolab_type == "OpenClaw"
        assert summary.network_type == "PRIVATE"
