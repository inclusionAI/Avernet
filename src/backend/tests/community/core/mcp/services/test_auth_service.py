"""Tests for MCPAuthService."""
from unittest.mock import MagicMock

from agentclaw.community.core.mcp.services.auth_service import MCPAuthService


class TestMCPAuthService:
    """MCPAuthService orchestrates permission checks using MCPCenter + auth client."""

    def test_check_mcp_permission_detail_public_server(self):
        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "serverCode": "mcp.test",
            "accessLevel": "PUBLIC",
            "tools": [{"name": "tool1"}],
        }
        mock_auth = MagicMock()
        mock_auth.query_permission_status.return_value = [
            {
                "resource_code": "mcp.test",
                "auth_state": "AUTHORIZED",
                "auth_state_name": "已授权",
            },
            {
                "resource_code": "mcp.test/tool1",
                "auth_state": "AUTHORIZED",
                "auth_state_name": "已授权",
            },
        ]
        svc = MCPAuthService(auth_client=mock_auth, mcp_center=mock_center)
        result = svc.check_mcp_permission_detail("user1", "mcp.test")

        assert result["has_permission"] is True
        assert result["access_level"] == "PUBLIC"
        assert result["tool_permissions"]["tool1"]["code"] == "AUTHORIZED"
        mock_auth.query_permission_status.assert_called_once_with(
            staff_no="user1",
            service_code="mcp.test",
            tool_list=["tool1"],
            is_public=True,
        )

    def test_check_mcp_permission_detail_mcp_center_failure(self):
        mock_center = MagicMock()
        mock_center.get_mcp_detail.side_effect = Exception("timeout")
        mock_auth = MagicMock()
        svc = MCPAuthService(auth_client=mock_auth, mcp_center=mock_center)
        result = svc.check_mcp_permission_detail("user1", "mcp.test")
        assert result["has_permission"] is True  # fallback
        assert result["access_level"] == ""

    def test_check_mcp_permission_detail_server_not_found(self):
        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = None
        mock_auth = MagicMock()
        svc = MCPAuthService(auth_client=mock_auth, mcp_center=mock_center)
        result = svc.check_mcp_permission_detail("user1", "mcp.test")
        assert result["has_permission"] is False
        assert result["tool_permissions"] == {}
        mock_auth.query_permission_status.assert_not_called()

    def test_apply_permission_delegates_to_auth_client(self):
        mock_center = MagicMock()
        mock_auth = MagicMock()
        mock_auth.apply_permission.return_value = {
            "success": True,
            "process_url": "http://example.com",
            "error": None,
        }
        svc = MCPAuthService(auth_client=mock_auth, mcp_center=mock_center)
        result = svc.apply_permission(
            staff_no="user1",
            service_code="mcp.test",
            tool_list=["tool1"],
            is_public=False,
            reason="need it",
        )
        assert result["success"] is True
        mock_auth.apply_permission.assert_called_once_with(
            staff_no="user1",
            service_code="mcp.test",
            tool_list=["tool1"],
            is_public=False,
            reason="need it",
        )

    def test_check_local_mcp_permission_does_not_call_auth_client(self):
        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "serverCode": "mcp.local.demo",
            "accessLevel": "LOCAL",
            "runMode": "LOCAL",
            "source": "local",
            "tools": [{"name": "tool1"}],
        }
        mock_auth = MagicMock()
        svc = MCPAuthService(auth_client=mock_auth, mcp_center=mock_center)

        result = svc.check_mcp_permission_detail("user1", "mcp.local.demo")

        assert result == {
            "has_permission": True,
            "access_level": "LOCAL",
            "tool_permissions": {},
        }
        mock_auth.query_permission_status.assert_not_called()

    def test_apply_local_mcp_permission_does_not_call_auth_client(self):
        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "serverCode": "mcp.local.demo",
            "accessLevel": "LOCAL",
            "runMode": "LOCAL",
            "source": "local",
        }
        mock_auth = MagicMock()
        svc = MCPAuthService(auth_client=mock_auth, mcp_center=mock_center)

        result = svc.apply_permission(
            staff_no="user1",
            service_code="mcp.local.demo",
            tool_list=["tool1"],
            is_public=False,
            reason="need it",
        )

        assert result == {"success": True, "process_url": None, "error": None}
        mock_auth.apply_permission.assert_not_called()

    def test_check_local_mcp_permission_by_run_mode_snake_case(self):
        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "serverCode": "mcp.local.demo",
            "run_mode": "LOCAL",
            "tools": [{"name": "tool1"}],
        }
        mock_auth = MagicMock()
        svc = MCPAuthService(auth_client=mock_auth, mcp_center=mock_center)

        result = svc.check_mcp_permission_detail("user1", "mcp.local.demo")

        assert result == {
            "has_permission": True,
            "access_level": "LOCAL",
            "tool_permissions": {},
        }
        mock_auth.query_permission_status.assert_not_called()

    def test_apply_permission_delegates_when_mcp_center_fails(self):
        mock_center = MagicMock()
        mock_center.get_mcp_detail.side_effect = RuntimeError("center down")
        mock_auth = MagicMock()
        mock_auth.apply_permission.return_value = {
            "success": True,
            "process_url": "http://example.com",
            "error": None,
        }
        svc = MCPAuthService(auth_client=mock_auth, mcp_center=mock_center)

        result = svc.apply_permission(
            staff_no="user1",
            service_code="mcp.test",
            tool_list=["tool1"],
            is_public=True,
            reason="need it",
        )

        assert result["success"] is True
        mock_auth.apply_permission.assert_called_once_with(
            staff_no="user1",
            service_code="mcp.test",
            tool_list=["tool1"],
            is_public=True,
            reason="need it",
        )
