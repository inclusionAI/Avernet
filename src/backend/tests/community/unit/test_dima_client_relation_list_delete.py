"""Unit tests for WorkspaceHostingClient.list_work_item_relations and
delete_work_item_relation, plus WorkspaceHostingWorkItemService delegation.

Covers:
- list_work_item_relations: ok_with_data, ok_default_operator, api_error, request_exception
- delete_work_item_relation: ok_empty_data, api_error, request_exception
- WorkspaceHostingWorkItemService: delegation for both methods
"""
from __future__ import annotations

import json
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
        "code": "ARK_RS_100000200",
        "message": "",
        "data": data,
    }
    return resp


# ── list_work_item_relations ──────────────────────────────────────────────


@pytest.mark.unit
class TestListWorkItemRelations:
    def test_ok_with_data(self):
        client = _make_client()
        canned_data = {
            "URL": [{"relationRecordId": "2024041800100538244", "url": "https://example.com"}],
            "SUB": [],
        }
        client.session.request.return_value = _mock_ok_response(data=canned_data)

        result = client.list_work_item_relations(
            work_item_id="2026091100118991620",
            operator="382716",
        )

        assert result["success"] is True
        assert result["data"]["URL"][0]["relationRecordId"] == "2024041800100538244"

        call_args = client.session.request.call_args
        assert call_args[1]["method"] == "GET"
        assert "workItem/relation/record/list" in call_args[1]["url"]
        assert call_args[1]["params"]["workItemId"] == "2026091100118991620"
        assert call_args[1]["params"]["operator"] == "382716"

    def test_ok_default_operator(self):
        """No operator passed → defaults to '100000'."""
        client = _make_client()
        client.session.request.return_value = _mock_ok_response(data={})

        client.list_work_item_relations(work_item_id="2026091100118991620")

        call_args = client.session.request.call_args
        assert call_args[1]["params"]["operator"] == "100000"

    def test_api_error(self):
        """Upstream returns success=false → _make_request raises."""
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
            client.list_work_item_relations(work_item_id="2026091100118991620")

    def test_request_exception(self):
        """Network error → _make_request raises."""
        client = _make_client()
        client.session.request.side_effect = requests.exceptions.ConnectionError("timeout")

        with pytest.raises(Exception, match="Request failed"):
            client.list_work_item_relations(work_item_id="2026091100118991620")


# ── delete_work_item_relation ─────────────────────────────────────────────


@pytest.mark.unit
class TestDeleteWorkItemRelation:
    def test_ok_empty_data(self):
        """Delete success returns empty data (allow_empty_data=True)."""
        client = _make_client()
        client.session.request.return_value = _mock_ok_response(data=None)

        result = client.delete_work_item_relation(
            operator="382716",
            request_body={"relationIdentifier": "URL", "relationRecordId": "2024041800100538244"},
        )

        assert result["success"] is True

        call_args = client.session.request.call_args
        assert call_args[1]["method"] == "POST"
        assert "workItem/relation/record/delete" in call_args[1]["url"]
        assert call_args[1]["params"]["operator"] == "382716"

        body = json.loads(call_args[1]["data"])
        assert body["relationIdentifier"] == "URL"
        assert body["relationRecordId"] == "2024041800100538244"

    def test_api_error(self):
        """Upstream returns success=false → _make_request raises."""
        client = _make_client()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "success": False,
            "code": "ARK_RS_100000404",
            "message": "Record not found",
        }
        client.session.request.return_value = resp

        with pytest.raises(Exception, match="DIMA API error"):
            client.delete_work_item_relation(
                operator="382716",
                request_body={"relationIdentifier": "URL", "relationRecordId": "nonexistent"},
            )

    def test_request_exception(self):
        """Network error → _make_request raises."""
        client = _make_client()
        client.session.request.side_effect = requests.exceptions.ConnectionError("timeout")

        with pytest.raises(Exception, match="Request failed"):
            client.delete_work_item_relation(
                operator="382716",
                request_body={"relationIdentifier": "URL", "relationRecordId": "2024041800100538244"},
            )


# ── WorkspaceHostingWorkItemService delegation ────────────────────────────


@pytest.mark.unit
class TestWorkItemServiceDelegation:
    def test_list_delegates_to_client(self):
        """WorkspaceHostingWorkItemService.list_work_item_relations delegates to client."""
        mock_client = MagicMock()
        mock_client.list_work_item_relations.return_value = {
            "success": True,
            "code": "200",
            "message": "OK",
            "data": {"URL": []},
        }

        service = WorkspaceHostingWorkItemService(client=mock_client)
        result = service.list_work_item_relations(
            work_item_id="2026091100118991620",
            operator="382716",
        )

        assert result["success"] is True
        mock_client.list_work_item_relations.assert_called_once_with(
            work_item_id="2026091100118991620",
            operator="382716",
        )

    def test_list_delegates_with_default_operator(self):
        """Default operator used when omitted."""
        mock_client = MagicMock()
        mock_client.list_work_item_relations.return_value = {
            "success": True,
            "code": "200",
            "message": "OK",
            "data": {},
        }

        service = WorkspaceHostingWorkItemService(client=mock_client)
        service.list_work_item_relations(work_item_id="2026091100118991620")

        mock_client.list_work_item_relations.assert_called_once_with(
            work_item_id="2026091100118991620",
            operator="100000",
        )

    def test_delete_delegates_to_client(self):
        """WorkspaceHostingWorkItemService.delete_work_item_relation delegates to client."""
        mock_client = MagicMock()
        mock_client.delete_work_item_relation.return_value = {
            "success": True,
            "code": "200",
            "message": "OK",
            "data": None,
        }

        service = WorkspaceHostingWorkItemService(client=mock_client)
        body = {"relationIdentifier": "URL", "relationRecordId": "2024041800100538244"}
        result = service.delete_work_item_relation(operator="382716", request_body=body)

        assert result["success"] is True
        mock_client.delete_work_item_relation.assert_called_once_with(
            operator="382716",
            request_body=body,
        )
