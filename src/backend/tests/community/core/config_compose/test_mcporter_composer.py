"""Unit tests for McporterComposer (backend MCP assembly, secrets inlined)."""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from agentclaw.community.core.config_compose.models import McpComposeInput
from agentclaw.community.core.config_compose.services.mcporter_composer import (
    TECLAW_MCP_NETWORK_PRIORITY,
    McporterComposeError,
    McporterComposer,
    mcp_network_priority_for,
)


SECRET_TOKEN = "supersecrettoken-inlined-123"


def _remote_mcp(server_code: str = "weather", protocol: str = "STREAMABLE_HTTP") -> dict:
    return {
        "server_code": server_code,
        "name": "Weather MCP",
        "run_mode": "REMOTE",
        "endpoints": [
            {
                "networkType": "INTERNET",
                "env": "PROD",
                "transportProtocol": protocol,
                "url": "https://mcp.example.com/weather",
            }
        ],
    }


def _manifest_json(manifest) -> str:
    return json.dumps(asdict(manifest), ensure_ascii=False)


def test_remote_header_secret_is_inlined_into_headers() -> None:
    composer = McporterComposer()
    item = McpComposeInput(
        mcp_data=_remote_mcp(),
        api_key=f"x-ling-auth={SECRET_TOKEN}",
        headers={"content-type": "application/json"},
        endpoint_env="PROD",
    )
    server = composer.compose_server(item)

    # secret inlined into the header (device-path shape); no auth_ref field exists
    assert not hasattr(server, "auth_ref")
    assert server.headers == {
        "content-type": "application/json",
        "x-ling-auth": SECRET_TOKEN,
    }
    # URL untouched for the header-secret case
    assert server.endpoint == "https://mcp.example.com/weather"
    assert server.transport == "http"
    # the inlined token IS present in the serialized manifest (no longer stripped)
    from agentclaw.community.kernel.bot_config import McpManifest

    assert SECRET_TOKEN in _manifest_json(McpManifest(servers=[server]))


def test_remote_without_secret_has_clean_headers() -> None:
    composer = McporterComposer()
    item = McpComposeInput(
        mcp_data=_remote_mcp(),
        api_key=None,
        headers={"content-type": "application/json"},
        endpoint_env="PROD",
    )
    server = composer.compose_server(item)
    assert server.headers == {"content-type": "application/json"}


def test_authorization_api_key_is_appended_to_url() -> None:
    composer = McporterComposer()
    item = McpComposeInput(
        mcp_data=_remote_mcp(),
        api_key=f"authorization={SECRET_TOKEN}",
        endpoint_env="PROD",
    )
    server = composer.compose_server(item)
    # authorization rides in the URL query (device-path shape)
    assert server.endpoint == f"https://mcp.example.com/weather?authorization={SECRET_TOKEN}"
    assert server.headers == {}


def test_unknown_api_key_name_is_ignored_for_parity() -> None:
    """Device-path parity: only authorization/x-ling-auth are special; any other
    ``name=value`` is dropped (not inlined into URL or headers)."""
    composer = McporterComposer()
    item = McpComposeInput(
        mcp_data=_remote_mcp(),
        api_key=f"x-custom-key={SECRET_TOKEN}",
        endpoint_env="PROD",
    )
    server = composer.compose_server(item)
    assert server.endpoint == "https://mcp.example.com/weather"
    assert server.headers == {}
    assert SECRET_TOKEN not in (server.endpoint or "")


def test_transport_protocol_preference_selects_sse() -> None:
    composer = McporterComposer()
    md = {
        "server_code": "multi",
        "run_mode": "REMOTE",
        "endpoints": [
            {"networkType": "INTERNET", "env": "PROD", "transportProtocol": "STREAMABLE_HTTP", "url": "https://h"},
            {"networkType": "INTERNET", "env": "PROD", "transportProtocol": "SSE", "url": "https://s"},
        ],
    }
    server = composer.compose_server(
        McpComposeInput(mcp_data=md, endpoint_env="PROD", transport_protocol="SSE")
    )
    assert server.endpoint == "https://s"
    assert server.transport == "sse"


def test_default_prefers_streamable_http() -> None:
    composer = McporterComposer()
    md = {
        "server_code": "multi",
        "run_mode": "REMOTE",
        "endpoints": [
            {"networkType": "INTERNET", "env": "PROD", "transportProtocol": "SSE", "url": "https://s"},
            {"networkType": "INTERNET", "env": "PROD", "transportProtocol": "STREAMABLE_HTTP", "url": "https://h"},
        ],
    }
    server = composer.compose_server(McpComposeInput(mcp_data=md, endpoint_env="PROD"))
    assert server.endpoint == "https://h"
    assert server.transport == "http"


def test_endpoints_as_json_string_is_parsed() -> None:
    composer = McporterComposer()
    md = {
        "server_code": "weather",
        "run_mode": "REMOTE",
        "endpoints": json.dumps(
            [{"networkType": "OFFICE", "env": "PROD", "transportProtocol": "SSE", "url": "https://o"}]
        ),
    }
    server = composer.compose_server(McpComposeInput(mcp_data=md, endpoint_env="PROD"))
    assert server.endpoint == "https://o"


def test_no_matching_env_endpoint_raises() -> None:
    composer = McporterComposer()
    md = {
        "server_code": "weather",
        "run_mode": "REMOTE",
        "endpoints": [{"networkType": "INTERNET", "env": "PRE", "transportProtocol": "SSE", "url": "https://x"}],
    }
    with pytest.raises(McporterComposeError):
        composer.compose_server(McpComposeInput(mcp_data=md, endpoint_env="PROD"))


def test_local_stdio_raises_as_unmodeled() -> None:
    composer = McporterComposer()
    md = {"server_code": "fs", "run_mode": "LOCAL", "stdio_configs": [{"command": "node"}]}
    with pytest.raises(McporterComposeError):
        composer.compose_server(McpComposeInput(mcp_data=md))


def test_compose_skips_local_stdio_servers() -> None:
    composer = McporterComposer()
    manifest = composer.compose(
        [
            McpComposeInput(mcp_data=_remote_mcp("remote"), endpoint_env="PROD"),
            McpComposeInput(
                mcp_data={
                    "server_code": "hitl",
                    "runMode": "LOCAL",
                    "stdioConfigs": [
                        {
                            "command": "python3",
                            "arguments": ["/home/admin/hitl/hitl_mcp_server.py"],
                        }
                    ],
                }
            ),
        ]
    )

    assert [s.server_code for s in manifest.servers] == ["remote"]


def test_missing_server_code_raises() -> None:
    composer = McporterComposer()
    with pytest.raises(McporterComposeError):
        composer.compose_server(McpComposeInput(mcp_data={"name": "x"}))


def test_compose_builds_manifest_for_multiple_servers() -> None:
    composer = McporterComposer()
    manifest = composer.compose(
        [
            McpComposeInput(mcp_data=_remote_mcp("a"), endpoint_env="PROD"),
            McpComposeInput(mcp_data=_remote_mcp("b"), endpoint_env="PROD"),
        ]
    )
    assert [s.server_code for s in manifest.servers] == ["a", "b"]


# ── teclaw network-priority selection (OFFICE > INTERNET > INTRANET; http>sse) ──

def _ep(network: str, protocol: str, url: str, env: str = "PROD") -> dict:
    return {"networkType": network, "env": env, "transportProtocol": protocol, "url": url}


def _multi(*endpoints: dict) -> dict:
    return {"server_code": "multi", "run_mode": "REMOTE", "endpoints": list(endpoints)}


def _teclaw_item(md: dict) -> McpComposeInput:
    # transport_protocol="SSE" proves the fixed priority ignores the per-MCP
    # preference for teclaw (it would pick SSE on the legacy path).
    return McpComposeInput(
        mcp_data=md,
        endpoint_env="PROD",
        transport_protocol="SSE",
        network_priority=TECLAW_MCP_NETWORK_PRIORITY,
    )


def test_mcp_network_priority_for_teclaw_only() -> None:
    assert mcp_network_priority_for("teclaw") == ("OFFICE", "INTERNET", "INTRANET")
    assert mcp_network_priority_for("openclaw") is None
    assert mcp_network_priority_for("moltis") is None
    assert mcp_network_priority_for(None) is None


def test_teclaw_network_primary_office_beats_internet_streamable() -> None:
    # OFFICE+SSE must win over INTERNET+STREAMABLE_HTTP — network is primary.
    server = McporterComposer().compose_server(
        _teclaw_item(_multi(
            _ep("INTERNET", "STREAMABLE_HTTP", "https://internet-http"),
            _ep("OFFICE", "SSE", "https://office-sse"),
        ))
    )
    assert server.endpoint == "https://office-sse"
    assert server.transport == "sse"


def test_teclaw_transport_breaks_tie_within_network() -> None:
    # Same network (OFFICE): STREAMABLE_HTTP beats SSE.
    server = McporterComposer().compose_server(
        _teclaw_item(_multi(
            _ep("OFFICE", "SSE", "https://office-sse"),
            _ep("OFFICE", "STREAMABLE_HTTP", "https://office-http"),
        ))
    )
    assert server.endpoint == "https://office-http"
    assert server.transport == "http"


def test_teclaw_internet_beats_intranet() -> None:
    server = McporterComposer().compose_server(
        _teclaw_item(_multi(
            _ep("INTRANET", "STREAMABLE_HTTP", "https://intranet-http"),
            _ep("INTERNET", "SSE", "https://internet-sse"),
        ))
    )
    assert server.endpoint == "https://internet-sse"


def test_teclaw_intranet_is_a_valid_candidate() -> None:
    # Legacy path drops INTRANET entirely; the teclaw path keeps it as the
    # lowest-priority fallback when it's the only network present.
    server = McporterComposer().compose_server(
        _teclaw_item(_multi(_ep("INTRANET", "STREAMABLE_HTTP", "https://intranet-http")))
    )
    assert server.endpoint == "https://intranet-http"
    assert server.transport == "http"


def test_teclaw_respects_endpoint_env() -> None:
    server = McporterComposer().compose_server(
        _teclaw_item(_multi(
            _ep("OFFICE", "STREAMABLE_HTTP", "https://office-pre", env="PRE"),
            _ep("INTRANET", "SSE", "https://intranet-prod", env="PROD"),
        ))
    )
    # Only the PROD endpoint is eligible, even though the PRE one ranks higher.
    assert server.endpoint == "https://intranet-prod"


def test_teclaw_no_eligible_network_raises() -> None:
    # An endpoint on an unknown/unreachable network is not a candidate.
    with pytest.raises(McporterComposeError):
        McporterComposer().compose_server(
            _teclaw_item(_multi(_ep("PUBLIC_VPC", "STREAMABLE_HTTP", "https://x")))
        )


def test_legacy_path_still_excludes_intranet() -> None:
    # Regression guard: without network_priority (non-teclaw), an INTRANET-only
    # server has no usable endpoint — unchanged legacy behavior.
    md = _multi(_ep("INTRANET", "STREAMABLE_HTTP", "https://intranet-http"))
    with pytest.raises(McporterComposeError):
        McporterComposer().compose_server(
            McpComposeInput(mcp_data=md, endpoint_env="PROD")
        )
