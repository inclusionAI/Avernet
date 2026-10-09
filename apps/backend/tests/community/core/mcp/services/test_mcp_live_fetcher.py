"""Tests for mcp_live_fetcher."""
import json
from unittest.mock import MagicMock, patch

from agentclaw.community.core.mcp.services.mcp_live_fetcher import (
    fetch_tools_from_endpoint,
    fetch_tools_live,
    _pick_best_endpoint,
)


class TestPickBestEndpoint:
    """Tests for _pick_best_endpoint."""

    def test_prefers_streamable_http(self):
        endpoints = [
            {"networkType": "INTERNET", "url": "https://sse.example.com", "transportProtocol": "SSE"},
            {"networkType": "OFFICE", "url": "https://http.example.com", "transportProtocol": "STREAMABLE_HTTP"},
        ]
        result = _pick_best_endpoint(endpoints)
        assert result == "https://http.example.com"

    def test_falls_back_to_sse(self):
        endpoints = [
            {"networkType": "INTERNET", "url": "https://sse.example.com", "transportProtocol": "SSE"},
        ]
        result = _pick_best_endpoint(endpoints)
        assert result == "https://sse.example.com"

    def test_skips_intranet(self):
        endpoints = [
            {"networkType": "INTRANET", "url": "https://internal.example.com", "transportProtocol": "SSE"},
        ]
        result = _pick_best_endpoint(endpoints)
        assert result is None

    def test_empty_list_returns_none(self):
        assert _pick_best_endpoint([]) is None


class TestFetchToolsFromEndpoint:
    """Tests for fetch_tools_from_endpoint."""

    def test_success_returns_tools(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": "agentclaw-live-fetch",
            "result": {
                "tools": [
                    {"name": "tool_a", "description": "desc a"},
                    {"name": "tool_b", "description": "desc b"},
                ]
            }
        }

        with patch("agentclaw.community.core.mcp.services.mcp_live_fetcher.requests.post", return_value=mock_response):
            tools = fetch_tools_from_endpoint("https://example.com/mcp", access_token="test_token")

        assert tools is not None
        assert len(tools) == 2
        assert tools[0]["name"] == "tool_a"

    def test_no_auth_header_when_token_missing(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {"tools": []}
        }

        with patch("agentclaw.community.core.mcp.services.mcp_live_fetcher.requests.post", return_value=mock_response) as mock_post:
            fetch_tools_from_endpoint("https://example.com/mcp")

        call_args = mock_post.call_args
        headers = call_args[1]["headers"]
        assert "Authorization" not in headers

    def test_http_error_returns_none(self):
        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("agentclaw.community.core.mcp.services.mcp_live_fetcher.requests.post", return_value=mock_response):
            tools = fetch_tools_from_endpoint("https://example.com/mcp", access_token="test_token")

        assert tools is None

    def test_json_rpc_error_returns_none(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "error": {"code": -52302, "message": "auth failed"},
        }

        with patch("agentclaw.community.core.mcp.services.mcp_live_fetcher.requests.post", return_value=mock_response):
            tools = fetch_tools_from_endpoint("https://example.com/mcp", access_token="test_token")

        assert tools is None

    def test_request_exception_returns_none(self):
        import requests
        with patch("agentclaw.community.core.mcp.services.mcp_live_fetcher.requests.post", side_effect=requests.exceptions.RequestException("network error")):
            tools = fetch_tools_from_endpoint("https://example.com/mcp")

        assert tools is None

    def test_unexpected_tools_type_returns_none(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {"tools": "not_a_list"},
        }

        with patch("agentclaw.community.core.mcp.services.mcp_live_fetcher.requests.post", return_value=mock_response):
            tools = fetch_tools_from_endpoint("https://example.com/mcp")

        assert tools is None

    def test_sse_response_parsing(self):
        """STREAMABLE_HTTP may return SSE format with event: message + data: {...}"""
        init_response = MagicMock()
        init_response.status_code = 200
        init_response.headers = {"Mcp-Session-Id": "sess-123"}
        init_response.json.return_value = {}  # not used because session_id in header

        tools_response = MagicMock()
        tools_response.status_code = 200
        tools_response.headers = {"Content-Type": "text/event-stream"}
        tools_response.content = (
            "event: message\n"
            "data: {\"jsonrpc\":\"2.0\",\"id\":\"agentclaw-live-fetch\","
            "\"result\":{\"tools\":[{\"name\":\"sse_tool\"}]}}\n"
        ).encode("utf-8")

        with patch(
            "agentclaw.community.core.mcp.services.mcp_live_fetcher.requests.post",
            side_effect=[init_response, MagicMock(), tools_response],
        ):
            tools = fetch_tools_from_endpoint("https://example.com/mcp")

        assert tools is not None
        assert len(tools) == 1
        assert tools[0]["name"] == "sse_tool"

    def test_sse_multi_line_data_parsing(self):
        """Gateway may split large JSON across multiple lines without data: prefix."""
        init_response = MagicMock()
        init_response.status_code = 200
        init_response.headers = {"Mcp-Session-Id": "sess-123"}

        tools_response = MagicMock()
        tools_response.status_code = 200
        tools_response.headers = {"Content-Type": "text/event-stream"}
        # Simulates gateway splitting JSON arbitrarily across lines
        tools_response.content = (
            "event: message\n"
            'data: {"jsonrpc":"2.0","id":"agentclaw-live-fetch","result":{"tools":['
            '{"name":"tool_a","description":"first half of a very long desc..."'
            ',"inputSchema":{"type":"object"}}]}}\n'
        ).encode("utf-8")

        with patch(
            "agentclaw.community.core.mcp.services.mcp_live_fetcher.requests.post",
            side_effect=[init_response, MagicMock(), tools_response],
        ):
            tools = fetch_tools_from_endpoint("https://example.com/mcp")

        assert tools is not None
        assert len(tools) == 1
        assert tools[0]["name"] == "tool_a"


class TestFetchToolsLive:
    """Tests for fetch_tools_live."""

    def test_success_replaces_tools(self):
        center_data = {
            "serverCode": "mcp.test",
            "endpoints": [
                {"networkType": "INTERNET", "url": "https://example.com/mcp", "transportProtocol": "STREAMABLE_HTTP"},
            ],
        }

        with patch("agentclaw.community.core.mcp.services.mcp_live_fetcher.fetch_tools_from_endpoint", return_value=[{"name": "live_tool"}]):
            tools = fetch_tools_live(center_data, access_token="token")

        assert tools is not None
        assert len(tools) == 1
        assert tools[0]["name"] == "live_tool"

    def test_no_endpoints_returns_none(self):
        center_data = {"serverCode": "mcp.test"}
        tools = fetch_tools_live(center_data)
        assert tools is None

    def test_string_endpoints_parsed(self):
        center_data = {
            "serverCode": "mcp.test",
            "endpoints": json.dumps([
                {"networkType": "INTERNET", "url": "https://example.com/mcp", "transportProtocol": "SSE"},
            ]),
        }

        with patch("agentclaw.community.core.mcp.services.mcp_live_fetcher.fetch_tools_from_endpoint", return_value=[{"name": "t"}]):
            tools = fetch_tools_live(center_data)

        assert tools is not None

    def test_fetch_failure_returns_none(self):
        center_data = {
            "serverCode": "mcp.test",
            "endpoints": [
                {"networkType": "INTERNET", "url": "https://example.com/mcp", "transportProtocol": "STREAMABLE_HTTP"},
            ],
        }

        with patch("agentclaw.community.core.mcp.services.mcp_live_fetcher.fetch_tools_from_endpoint", return_value=None):
            tools = fetch_tools_live(center_data, access_token="token")

        assert tools is None
