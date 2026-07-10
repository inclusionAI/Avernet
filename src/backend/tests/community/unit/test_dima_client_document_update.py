"""Unit tests for WorkspaceHostingClient.update_work_item_document and service delegation.

Covers:
- WorkspaceHostingClient: ok, api_error, request_exception
- WorkspaceHostingWorkItemService: delegation to client
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_client import WorkspaceHostingClient
from agentclaw.community.core.aicoding.services.workspace_hosting_workitem_service import WorkspaceHostingWorkItemService


def _make_client() -> WorkspaceHostingClient:
    """Create a WorkspaceHostingClient instance without DI."""
    client = WorkspaceHostingClient.__new__(WorkspaceHostingClient)
    client.base_url = "https://devapi.teamclaw.com"
    client.access_key = "dummy-access-key"
    client.access_secret = "dummy-secret-16b"
    client.tenant = "alipay"
    client.timeout = 30
    client.session = MagicMock()
    return client


def _mock_ok_response(data=None):
    """Build a mock response that returns success JSON."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "success": True,
        "code": "200",
        "message": "OK",
        "data": data,
    }
    return resp


@pytest.mark.unit
class TestWorkspaceHostingClientUpdateWorkItemDocument:
    def test_ok_with_data(self):
        client = _make_client()
        client.session.request.return_value = _mock_ok_response(
            data={"workItemId": "20240806001"},
        )

        result = client.update_work_item_document(
            staff_id="038271",
            work_item_id="20240806001",
            content="<p>hello</p>",
            format_type="RICHTEXT",
            editor_type="YUQUE",
        )

        assert result["success"] is True
        assert result["data"]["workItemId"] == "20240806001"

        # 验证 _make_request 传参正确
        call_args = client.session.request.call_args
        assert call_args[1]["method"] == "POST"
        assert "workItem/document/update" in call_args[1]["url"]
        assert call_args[1]["params"]["staffId"] == "038271"
        assert call_args[1]["params"]["workItemId"] == "20240806001"

    def test_ok_empty_data(self):
        """上游返回 data=null（allow_empty_data=True 不抛异常）。"""
        client = _make_client()
        client.session.request.return_value = _mock_ok_response(data=None)

        result = client.update_work_item_document(
            staff_id="038271",
            work_item_id="20240806001",
            content="markdown content",
        )

        assert result["success"] is True

    def test_default_format_type(self):
        """不传 formatType 时默认 MARKDOWN。"""
        client = _make_client()
        client.session.request.return_value = _mock_ok_response()

        client.update_work_item_document(
            staff_id="038271",
            work_item_id="20240806001",
            content="text",
        )

        call_args = client.session.request.call_args
        import json
        body = json.loads(call_args[1]["data"])
        assert body["formatType"] == "MARKDOWN"
        assert body["editorType"] == "YUQUE"

    def test_api_error(self):
        """上游返回 success=false → _make_request 抛异常。"""
        client = _make_client()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "success": False,
            "code": "500",
            "message": "Internal error",
        }
        client.session.request.return_value = resp

        with pytest.raises(Exception, match="DIMA API error"):
            client.update_work_item_document(
                staff_id="038271",
                work_item_id="20240806001",
                content="text",
            )

    def test_request_exception(self):
        """网络异常 → _make_request 抛异常。"""
        client = _make_client()
        client.session.request.side_effect = requests.exceptions.ConnectionError("timeout")

        with pytest.raises(Exception, match="Request failed"):
            client.update_work_item_document(
                staff_id="038271",
                work_item_id="20240806001",
                content="text",
            )


@pytest.mark.unit
class TestWorkspaceHostingWorkItemServiceDelegation:
    def test_delegates_to_client(self):
        """WorkspaceHostingWorkItemService.update_work_item_document 委托给 client。"""
        mock_client = MagicMock()
        mock_client.update_work_item_document.return_value = {
            "success": True,
            "code": "200",
            "message": "OK",
            "data": None,
        }

        service = WorkspaceHostingWorkItemService(client=mock_client)
        result = service.update_work_item_document(
            staff_id="038271",
            work_item_id="20240806001",
            content="<p>test</p>",
            format_type="RICHTEXT",
            editor_type="YUQUE",
        )

        assert result["success"] is True
        mock_client.update_work_item_document.assert_called_once_with(
            staff_id="038271",
            work_item_id="20240806001",
            content="<p>test</p>",
            format_type="RICHTEXT",
            editor_type="YUQUE",
        )

    def test_delegates_with_defaults(self):
        """不传可选参数时使用默认值委托。"""
        mock_client = MagicMock()
        mock_client.update_work_item_document.return_value = {
            "success": True,
            "code": "200",
            "message": "OK",
            "data": None,
        }

        service = WorkspaceHostingWorkItemService(client=mock_client)
        service.update_work_item_document(
            staff_id="038271",
            work_item_id="20240806001",
            content="text",
        )

        mock_client.update_work_item_document.assert_called_once_with(
            staff_id="038271",
            work_item_id="20240806001",
            content="text",
            format_type="MARKDOWN",
            editor_type="YUQUE",
        )
