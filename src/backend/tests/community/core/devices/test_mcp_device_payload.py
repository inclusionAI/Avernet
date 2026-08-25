"""Tests for mcp_device_payload.convert_to_device_format branch coverage."""
import pytest

from agentclaw.community.core.devices.services.mcp_device_payload import convert_to_device_format


def test_local_stdio_mode():
    cfg = convert_to_device_format({
        "server_code": "x", "name": "X", "run_mode": "LOCAL",
        "stdio_configs": [{"command": "node", "arguments": ["a"], "envVariables": {"K": "V"}}],
    })
    assert cfg.transport == "stdio"
    assert cfg.command == "node"
    assert cfg.args == ["a"]
    assert cfg.env == {"K": "V"}


def test_remote_streamable_http_endpoint():
    cfg = convert_to_device_format({
        "server_code": "x", "run_mode": "REMOTE",
        "endpoints": [{"url": "http://e", "networkType": "INTERNET",
                       "env": "PROD", "transportProtocol": "STREAMABLE_HTTP"}],
    })
    assert cfg.transport == "http"
    assert cfg.url == "http://e"


def test_remote_no_valid_endpoints_raises():
    with pytest.raises(Exception, match="没有可用的"):
        convert_to_device_format({
            "server_code": "x", "run_mode": "REMOTE",
            # env mismatch → no valid endpoints for PROD
            "endpoints": [{"url": "http://e", "networkType": "OFFICE", "env": "PRE"}],
        })


def test_preferred_transport_protocol_falls_back():
    # user prefers SSE but only STREAMABLE_HTTP exists → falls back to first valid
    cfg = convert_to_device_format(
        {"server_code": "x", "run_mode": "REMOTE",
         "endpoints": [{"url": "http://e", "networkType": "INTERNET",
                        "env": "PROD", "transportProtocol": "STREAMABLE_HTTP"}]},
        transport_protocol="SSE",
    )
    assert cfg.url == "http://e"


def test_authorization_api_key_appended_to_url():
    cfg = convert_to_device_format(
        {"server_code": "x", "run_mode": "REMOTE",
         "endpoints": [{"url": "http://e", "networkType": "INTERNET",
                        "env": "PROD", "transportProtocol": "STREAMABLE_HTTP"}]},
        api_key="authorization=Bearer tok",
    )
    assert cfg.url == "http://e?authorization=Bearer tok"


def test_stdio_configs_as_json_string():
    cfg = convert_to_device_format({
        "server_code": "x", "run_mode": "LOCAL",
        "stdio_configs": '[{"command": "node", "arguments": ["a"]}]',
    })
    assert cfg.transport == "stdio"
    assert cfg.command == "node"


def test_endpoints_as_json_string():
    cfg = convert_to_device_format({
        "server_code": "x", "run_mode": "REMOTE",
        "endpoints": '[{"url": "http://e", "networkType": "INTERNET", '
                     '"env": "PROD", "transportProtocol": "STREAMABLE_HTTP"}]',
    })
    assert cfg.url == "http://e"


def test_malformed_json_strings_fall_back_to_empty():
    # invalid endpoints + stdio JSON → both decode to [] → REMOTE w/ no endpoints.
    cfg = convert_to_device_format({
        "server_code": "x", "run_mode": "REMOTE",
        "endpoints": "not-json", "stdio_configs": "also-not-json",
    })
    assert cfg.transport == "sse"
    assert cfg.url is None
