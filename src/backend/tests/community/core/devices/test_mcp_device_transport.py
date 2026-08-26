"""Tests for mcp_device_transport — already-exists fallback and retry logic.

收编后 MCP 5 函数走注入的 ``transport``(post/get/put/delete + 相对 path），
不再裸 httpx。用 FakeTransport 断言走了 transport，业务包装(409-as-update /
retry / listPolicy)不变。

The transport-agnostic helpers are shared Core code; concrete HTTP transports
remain in profile-specific composition roots. The moved Core
helper classifies HTTP failures via the transport-neutral exception aliases
re-exported from ``plugin_api.http_client`` (no ``httpx`` import in Core).
"""
import pytest
from unittest.mock import MagicMock, patch

from agentclaw.community.plugin_api.http_client import HttpClientRequestError
from agentclaw.community.core.devices.services import mcp_device_transport as mcp_transport
from agentclaw.community.core.devices.services.mcp_device_payload import (
    DeviceMCPConfig,
    is_already_exists_error,
)


def _make_config(server_code: str = "mcp.test.server") -> DeviceMCPConfig:
    return DeviceMCPConfig(
        server_code=server_code, transport="sse", url="http://mcp.test/sse", enabled=True,
    )


def _resp(status, text="", json_body=None):
    r = MagicMock(status_code=status, text=text)
    r.json.return_value = json_body if json_body is not None else {}
    return r


class FakeTransport:
    """Duck-typed transport：每 verb 返回预设 response（单个 / 列表按序 / 异常）。"""

    def __init__(self, responses):
        self._responses = responses
        self._i = 0
        self.calls = []

    def _next(self, verb, path):
        self.calls.append((verb, path))
        r = self._responses
        if isinstance(r, Exception):
            raise r
        if isinstance(r, list):
            cur = r[min(self._i, len(r) - 1)]
            self._i += 1
            if isinstance(cur, Exception):
                raise cur
            return cur
        return r

    def get(self, path):
        return self._next("GET", path)

    def post(self, path, *, json=None):
        return self._next("POST", path)

    def put(self, path, *, json=None):
        return self._next("PUT", path)

    def delete(self, path):
        return self._next("DELETE", path)


class TestIsAlreadyExistsError:
    def test_http_409(self):
        assert is_already_exists_error(Exception('Create failed: 409, {"detail":"Conflict"}')) is True

    def test_chinese_already_exists(self):
        assert is_already_exists_error(Exception('Create failed: 500, {"detail":"MCP server 已存在"}')) is True

    def test_english_already_exists(self):
        assert is_already_exists_error(Exception('Create failed: 500, {"detail":"MCP server already exists: x"}')) is True  # noqa: E501

    def test_arca_500_already_exists(self):
        e = Exception(
            'Create failed: 500, {"detail":"创建失败: mcp.config.create failed: '
            'MCP server already exists: mcp.ant.alipaybase-antlogsmcp.mcp-server"}'
        )
        assert is_already_exists_error(e) is True

    def test_other_error_not_matched(self):
        assert is_already_exists_error(Exception('Create failed: 500, {"detail":"Internal Server Error"}')) is False

    def test_network_error_not_matched(self):
        assert is_already_exists_error(Exception("Connection refused")) is False

    def test_404_not_matched(self):
        assert is_already_exists_error(Exception('Create failed: 404, {"detail":"Not Found"}')) is False


class TestPushSingleMcpAlreadyExistsFallback:
    def _patch_convert(self):
        return patch.object(mcp_transport, "convert_to_device_format", return_value=_make_config())

    def test_fallback_on_http_500_already_exists(self):
        t = FakeTransport(None)
        exists_err = Exception('Create failed: 500, {"detail":"MCP server already exists: x"}')
        with self._patch_convert(), \
                patch.object(mcp_transport, "_create_mcp", side_effect=exists_err), \
                patch.object(mcp_transport, "_update_mcp") as update:
            assert mcp_transport.push_single_mcp(t, {"server_code": "x"}) is True
        update.assert_called_once()

    def test_fallback_on_http_409(self):
        t = FakeTransport(None)
        with self._patch_convert(), \
                patch.object(mcp_transport, "_create_mcp",
                             side_effect=Exception('Create failed: 409, {"detail":"Conflict"}')), \
                patch.object(mcp_transport, "_update_mcp") as update:
            assert mcp_transport.push_single_mcp(t, {"server_code": "x"}) is True
        update.assert_called_once()

    def test_fallback_on_chinese_already_exists(self):
        t = FakeTransport(None)
        with self._patch_convert(), \
                patch.object(mcp_transport, "_create_mcp",
                             side_effect=Exception("Create failed: 500, MCP server 已存在")), \
                patch.object(mcp_transport, "_update_mcp") as update:
            assert mcp_transport.push_single_mcp(t, {"server_code": "x"}) is True
        update.assert_called_once()

    def test_no_fallback_on_other_errors(self):
        t = FakeTransport(None)
        with self._patch_convert(), \
                patch.object(mcp_transport, "_create_mcp",
                             side_effect=Exception("Create failed: 500, Internal Server Error")), \
                patch.object(mcp_transport, "_update_mcp") as update:
            with pytest.raises(Exception, match="Internal Server Error"):
                mcp_transport.push_single_mcp(t, {"server_code": "x"})
        update.assert_not_called()

    def test_create_success_no_update(self):
        t = FakeTransport(None)
        with self._patch_convert(), \
                patch.object(mcp_transport, "_create_mcp") as create, \
                patch.object(mcp_transport, "_update_mcp") as update:
            assert mcp_transport.push_single_mcp(t, {"server_code": "x"}) is True
        create.assert_called_once()
        update.assert_not_called()


class TestCreateMcpNoRetryOnAlreadyExists:
    def test_no_retry_on_500_already_exists(self):
        t = FakeTransport(_resp(500, '{"detail":"MCP server already exists: mcp.test.server"}'))
        with pytest.raises(Exception, match="already exists"):
            mcp_transport._create_mcp(t, _make_config())
        assert len(t.calls) == 1

    def test_no_retry_on_409(self):
        t = FakeTransport(_resp(409, '{"detail":"Conflict"}'))
        with pytest.raises(Exception, match="409"):
            mcp_transport._create_mcp(t, _make_config())
        assert len(t.calls) == 1

    def test_retry_on_other_500_errors(self):
        t = FakeTransport(_resp(500, '{"detail":"Internal Server Error"}'))
        with patch("time.sleep"):
            with pytest.raises(Exception, match="Create MCP failed after 3 attempts"):
                mcp_transport._create_mcp(t, _make_config())
        assert len(t.calls) == 3
        assert t.calls[0] == ("POST", "/api/mcp")


class TestProbeMcp:
    def test_present(self):
        assert mcp_transport.probe_mcp(FakeTransport(_resp(200)), "x") is True

    def test_absent(self):
        assert mcp_transport.probe_mcp(FakeTransport(_resp(404)), "x") is False

    def test_transport_error_is_absent(self):
        assert mcp_transport.probe_mcp(FakeTransport(HttpClientRequestError("boom")), "x") is False


class TestFilterServers:
    def test_success(self):
        t = FakeTransport(_resp(200, json_body={"success": True}))
        assert mcp_transport.filter_servers(t, [{"server_code": "a"}]) is True

    def test_device_reports_failure(self):
        t = FakeTransport(_resp(200, json_body={"success": False, "message": "nope"}))
        assert mcp_transport.filter_servers(t, []) is False

    def test_non_200(self):
        assert mcp_transport.filter_servers(FakeTransport(_resp(400, text="bad")), []) is False

    def test_retry_then_success(self):
        t = FakeTransport([_resp(500), _resp(200, json_body={"success": True})])
        with patch("time.sleep"):
            assert mcp_transport.filter_servers(t, [{"server_code": "a"}]) is True

    def test_request_error_returns_false(self):
        with patch("time.sleep"):
            assert mcp_transport.filter_servers(FakeTransport(HttpClientRequestError("net")), []) is False


class TestPushSingleRequestError:
    def test_request_error_wrapped_and_raised(self):
        with patch.object(mcp_transport, "convert_to_device_format", return_value=_make_config()), \
                patch.object(mcp_transport, "_create_mcp", side_effect=HttpClientRequestError("net")):
            with pytest.raises(Exception, match="Network error syncing"):
                mcp_transport.push_single_mcp(FakeTransport(None), {"server_code": "x"})


class TestRemoveMcp:
    def test_success(self):
        with patch.object(mcp_transport, "_delete_mcp") as d:
            assert mcp_transport.remove_mcp(FakeTransport(None), "x") is True
        d.assert_called_once()

    def test_failure_swallowed(self):
        with patch.object(mcp_transport, "_delete_mcp", side_effect=Exception("boom")):
            assert mcp_transport.remove_mcp(FakeTransport(None), "x") is False


class TestUpdateMcp:
    def test_success(self):
        mcp_transport._update_mcp(FakeTransport(_resp(200)), _make_config())  # no raise

    def test_retry_exhausted_raises(self):
        with patch("time.sleep"):
            with pytest.raises(Exception, match="Update MCP failed after 3 attempts"):
                mcp_transport._update_mcp(FakeTransport(_resp(500, text="err")), _make_config())


class TestDeleteMcp:
    def test_success_204(self):
        mcp_transport._delete_mcp(FakeTransport(_resp(204)), "x")  # no raise

    def test_404_is_success(self):
        mcp_transport._delete_mcp(FakeTransport(_resp(404)), "x")

    def test_retry_exhausted_swallows(self):
        with patch("time.sleep"):
            mcp_transport._delete_mcp(FakeTransport(_resp(500, text="err")), "x")  # logs + returns


