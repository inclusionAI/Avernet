"""Unit tests for WorkspaceHostingClient.upload_file_to_arkgw.

Covers all branches:
- ok_with_file: upload file content, returns success dict.
- ok_with_url: url转存, no file content.
- ok_with_file_and_url: both provided.
- invalid_response_format: response.json() returns non-dict → raises.
- request_exception: requests raises RequestException → raises.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_client import WorkspaceHostingClient


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


@pytest.mark.unit
class TestUploadFileToArkgw:
    def test_ok_with_file(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "code": "ARK_RS_100000200",
            "message": "",
            "data": {"fileId": "123", "fileName": "test.pdf"},
        }
        client.session.post.return_value = mock_response

        result = client.upload_file_to_arkgw(
            staff_id="100000",
            source_id="agentCoding",
            file_content=b"fake pdf content",
            file_name="test.pdf",
            content_type="application/pdf",
        )

        assert result["success"] is True
        assert result["code"] == "ARK_RS_100000200"
        assert result["data"]["fileId"] == "123"

        call_kwargs = client.session.post.call_args[1]
        assert call_kwargs["data"]["staffId"] == "100000"
        assert call_kwargs["data"]["sourceId"] == "agentCoding"
        assert call_kwargs["files"]["file"][0] == "test.pdf"
        assert "url" not in call_kwargs["data"]

    def test_ok_with_url(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "code": "ARK_RS_100000200",
            "message": "",
            "data": {"fileId": "456", "fileName": "image.png"},
        }
        client.session.post.return_value = mock_response

        result = client.upload_file_to_arkgw(
            staff_id="100000",
            source_id="agentCoding",
            url="https://example.com/image.png",
        )

        assert result["success"] is True
        assert result["data"]["fileId"] == "456"

        call_kwargs = client.session.post.call_args[1]
        assert call_kwargs["data"]["url"] == "https://example.com/image.png"
        assert call_kwargs["files"] is None

    def test_ok_with_file_and_url(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "code": "ARK_RS_100000200",
            "message": "",
            "data": {"fileId": "789"},
        }
        client.session.post.return_value = mock_response

        result = client.upload_file_to_arkgw(
            staff_id="100000",
            source_id="agentCoding",
            file_content=b"content",
            file_name="doc.pdf",
            content_type="application/pdf",
            url="https://example.com/img.png",
        )

        assert result["success"] is True
        call_kwargs = client.session.post.call_args[1]
        assert call_kwargs["data"]["url"] == "https://example.com/img.png"
        assert call_kwargs["files"]["file"][0] == "doc.pdf"

    def test_invalid_response_format(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = "not a dict"
        client.session.post.return_value = mock_response

        with pytest.raises(Exception, match="Arkgw returned invalid format"):
            client.upload_file_to_arkgw(
                staff_id="100000",
                source_id="agentCoding",
                file_content=b"content",
                file_name="test.pdf",
            )

    def test_request_exception(self):
        client = _make_client()
        client.session.post.side_effect = requests.exceptions.ConnectionError("timeout")

        with pytest.raises(Exception, match="Arkgw upload request failed"):
            client.upload_file_to_arkgw(
                staff_id="100000",
                source_id="agentCoding",
                file_content=b"content",
                file_name="test.pdf",
            )
