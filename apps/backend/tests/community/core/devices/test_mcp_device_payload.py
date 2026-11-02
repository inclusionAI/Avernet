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


def test_manifest_transport_protocol_does_not_fall_back():
    with pytest.raises(Exception, match="没有可用的 SSE"):
        convert_to_device_format(
            {
                "server_code": "x",
                "run_mode": "REMOTE",
                "endpoints": [
                    {
                        "url": "http://e",
                        "networkType": "INTERNET",
                        "env": "PROD",
                        "transportProtocol": "STREAMABLE_HTTP",
                    }
                ],
            },
            transport_protocol="SSE",
            strict_transport_protocol=True,
        )


def test_authorization_api_key_appended_to_url():
    cfg = convert_to_device_format(
        {"server_code": "x", "run_mode": "REMOTE",
         "endpoints": [{"url": "http://e", "networkType": "INTERNET",
                        "env": "PROD", "transportProtocol": "STREAMABLE_HTTP"}]},
        api_key="authorization=Bearer tok",
    )
    assert cfg.url == "http://e?authorization=Bearer tok"


def test_manifest_url_override_replaces_selected_endpoint_but_keeps_transport():
    cfg = convert_to_device_format(
        {
            "server_code": "x",
            "run_mode": "REMOTE",
            "endpoints": [
                {
                    "url": "http://center",
                    "networkType": "INTERNET",
                    "env": "PRE",
                    "transportProtocol": "STREAMABLE_HTTP",
                }
            ],
        },
        endpoint_env="PRE",
        transport_protocol="STREAMABLE_HTTP",
        url_override="https://custom.example/mcp",
        api_key="authorization=Bearer tok",
    )

    assert cfg.transport == "http"
    assert cfg.url == "https://custom.example/mcp?authorization=Bearer tok"


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


def test_malformed_json_endpoint_override_still_used():
    cfg = convert_to_device_format(
        {
            "server_code": "x", "run_mode": "REMOTE",
            "endpoints": "not-json", "stdio_configs": "also-not-json",
        },
        url_override="https://custom.example/mcp",
    )
    assert cfg.transport == "sse"
    assert cfg.url == "https://custom.example/mcp"


def test_endpoints_without_valid_no_override_still_fails():
    with pytest.raises(Exception, match="没有可用的"):
        convert_to_device_format(
            {
                "server_code": "x", "run_mode": "REMOTE",
                "endpoints": [
                    {"url": "https://center.example/other", "networkType": "INTERNET", "env": "DAY"},
                ],
            },
            endpoint_env="PROD",
        )


def test_no_center_endpoint_no_override_still_fails():
    with pytest.raises(Exception, match="没有可用的"):
        convert_to_device_format(
            {"server_code": "x", "run_mode": "REMOTE", "endpoints": []},
            endpoint_env="PROD",
        )


def test_endpoints_without_valid_override_still_used():
    cfg = convert_to_device_format(
        {
            "server_code": "x", "run_mode": "REMOTE",
            "endpoints": [
                {"url": "https://center.example/other", "networkType": "INTERNET", "env": "DAY"},
            ],
        },
        endpoint_env="PROD",
        url_override="https://custom.example/mcp",
    )
    assert cfg.transport == "sse"
    assert cfg.url == "https://custom.example/mcp"


def test_no_center_endpoint_override_still_used_with_ok_transport_protocol():
    cfg = convert_to_device_format(
        {"server_code": "x", "run_mode": "REMOTE", "endpoints": []},
        endpoint_env="PROD",
        url_override="https://custom.example/mcp",
    )
    assert cfg.url == "https://custom.example/mcp"
    assert cfg.transport == "sse"


def test_no_center_endpoint_override_still_used_when_protocol_is_valid_for_override():
    cfg = convert_to_device_format(
        {"server_code": "x", "run_mode": "REMOTE", "endpoints": []},
        endpoint_env="PROD",
        url_override="https://custom.example/mcp",
        strict_transport_protocol=True,
    )
    assert cfg.url == "https://custom.example/mcp"
    assert cfg.transport == "sse"
