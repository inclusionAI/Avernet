from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gateway.community.core.forwarding import DomainMap
from gateway.community.core.forwarding._domains import _expand_vars, _parse_servers

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "application.yaml"

_VARS = {
    "backend_server_url": "http://backend:8080",
    "baas_server_url": "http://baas:9090",
    "engine_proxy_server_url": "https://engineproxy:20003",
}


def _map() -> DomainMap:
    return DomainMap.from_config(
        {
            "base_path": "/openapi/v1",
            "domains": {
                "bots": {
                    "server": "backend",
                    "schema": {"source": "file", "path": "schemas/bots.openapi.json"},
                }
            },
            "servers": {"backend": {"base_url": "http://backend:8080"}},
        },
        variables={},
    )


def test_resolves_configured_domain() -> None:
    server = _map().resolve("/openapi/v1/bots/123/restart")
    assert server is not None
    assert server.name == "backend"
    assert server.base_url == "http://backend:8080"


def test_domain_at_root_resolves() -> None:
    assert _map().resolve("/openapi/v1/bots") is not None


def test_unknown_domain_returns_none() -> None:
    assert _map().resolve("/openapi/v1/unknown/x") is None


def test_path_outside_version_base_returns_none() -> None:
    assert _map().resolve("/api/bots/123") is None


def test_bare_version_base_has_no_domain() -> None:
    assert _map().resolve("/openapi/v1") is None


def test_schema_source_parsed() -> None:
    domain = _map().domain_for("/openapi/v1/bots/1")
    assert domain is not None
    assert domain.schema.source == "file"
    assert domain.schema.location == "schemas/bots.openapi.json"
    assert domain.schema.refresh_seconds == 300


def test_shipped_config_loads() -> None:
    raw = yaml.safe_load(_CONFIG.read_text())
    dm = DomainMap.from_config(raw["user_config"]["upstreams"], variables=_VARS)
    assert dm.domain_for("/openapi/v1/bots") is not None


# ── protocols ────────────────────────────────────────────────────────────────


def _protocol_map() -> DomainMap:
    return DomainMap.from_config(
        {
            "domains": {
                "bots": {"server": "backend"},
                "engine": {
                    "server": "proxy",
                    "protocols": ["websocket"],
                    "rewrite": {"from": "/openapi/v1/engine", "to": "/proxypass"},
                },
                "both": {"server": "backend", "protocols": ["http", "websocket"]},
            },
            "servers": {
                "backend": {"base_url": "http://backend:8080"},
                "proxy": {"base_url": "https://proxy:20003"},
            },
        },
        variables={},
    )


def test_protocols_default_to_http_only() -> None:
    """Every domain predating the declaration keeps behaving identically."""
    bots = _protocol_map().domain_for("/openapi/v1/bots/x")
    assert bots is not None
    assert bots.serves_http
    assert not bots.serves_websocket


def test_a_socket_domain_does_not_serve_http() -> None:
    engine = _protocol_map().domain_for("/openapi/v1/engine/t/ws")
    assert engine is not None
    assert engine.serves_websocket
    assert not engine.serves_http


def test_a_domain_may_declare_both_planes() -> None:
    both = _protocol_map().domain_for("/openapi/v1/both/x")
    assert both is not None
    assert both.serves_http and both.serves_websocket


def test_websocket_domains_lists_only_socket_domains() -> None:
    assert set(_protocol_map().websocket_domains()) == {"engine", "both"}


def test_unknown_protocol_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown protocol"):
        DomainMap.from_config(
            {
                "domains": {"x": {"server": "s", "protocols": ["grpc"]}},
                "servers": {"s": {"base_url": "http://s"}},
            },
            variables={},
        )


# ── rewrite ──────────────────────────────────────────────────────────────────


def test_no_rewrite_means_the_path_travels_verbatim() -> None:
    bots = _protocol_map().domain_for("/openapi/v1/bots/x")
    assert bots is not None
    assert bots.rewrite is None
    assert bots.upstream_path("/openapi/v1/bots/a%2Fb") == "/openapi/v1/bots/a%2Fb"


def test_a_declared_rewrite_substitutes_only_the_prefix() -> None:
    engine = _protocol_map().domain_for("/openapi/v1/engine/t/ws")
    assert engine is not None
    assert (
        engine.upstream_path("/openapi/v1/engine/ARCA_x@0:20003/api/openclaw/ws")
        == "/proxypass/ARCA_x@0:20003/api/openclaw/ws"
    )


def test_a_rewrite_never_re_encodes_the_tail() -> None:
    engine = _protocol_map().domain_for("/openapi/v1/engine/t/ws")
    assert engine is not None
    assert (
        engine.upstream_path("/openapi/v1/engine/ARCA%5Fx%400%3A2/api/x%20y")
        == "/proxypass/ARCA%5Fx%400%3A2/api/x%20y"
    )


def test_a_rewrite_anchored_off_the_domain_is_rejected() -> None:
    """A rule that could never fire is a config mistake, not a silent no-op."""
    with pytest.raises(ValueError, match="can never match"):
        DomainMap.from_config(
            {
                "domains": {
                    "engine": {
                        "server": "s",
                        "rewrite": {"from": "/somewhere/else", "to": "/proxypass"},
                    }
                },
                "servers": {"s": {"base_url": "http://s"}},
            },
            variables={},
        )


def test_a_rewrite_needs_both_ends() -> None:
    with pytest.raises(ValueError, match="needs both"):
        DomainMap.from_config(
            {
                "domains": {
                    "engine": {"server": "s", "rewrite": {"from": "/openapi/v1/engine"}}
                },
                "servers": {"s": {"base_url": "http://s"}},
            },
            variables={},
        )


# ── socket origin ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://proxy.example", "wss://proxy.example"),
        ("http://proxy.example:8080", "ws://proxy.example:8080"),
        ("wss://proxy.example", "wss://proxy.example"),
        ("ws://proxy.example", "ws://proxy.example"),
        ("HTTPS://proxy.example/", "wss://proxy.example"),
    ],
)
def test_socket_origin_is_derived_from_the_server_scheme(
    base_url: str, expected: str
) -> None:
    dm = DomainMap.from_config(
        {
            "domains": {"engine": {"server": "s", "protocols": ["websocket"]}},
            "servers": {"s": {"base_url": base_url}},
        },
        variables={},
    )
    engine = dm.domain_for("/openapi/v1/engine/x")
    assert engine is not None
    assert engine.websocket_base_url == expected


def test_a_socket_domain_without_a_usable_scheme_fails_at_startup() -> None:
    with pytest.raises(ValueError, match="no scheme a websocket can be opened with"):
        DomainMap.from_config(
            {
                "domains": {"engine": {"server": "s", "protocols": ["websocket"]}},
                "servers": {"s": {"base_url": "engineproxy.example.com"}},
            },
            variables={},
        )


def test_an_http_domain_needs_no_socket_scheme() -> None:
    """The bare-host samples the shipped config uses must keep loading."""
    dm = DomainMap.from_config(
        {
            "domains": {"bots": {"server": "s"}},
            "servers": {"s": {"base_url": "backend.sample.com"}},
        },
        variables={},
    )
    bots = dm.domain_for("/openapi/v1/bots")
    assert bots is not None
    assert bots.websocket_base_url == ""


def test_unknown_server_reference_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown server"):
        DomainMap.from_config(
            {"domains": {"bots": {"server": "ghost"}}, "servers": {}},
            variables={},
        )


def test_expand_vars_resolves_from_dict() -> None:
    result = _expand_vars("${backend_server_url}", _VARS)
    assert result == "http://backend:8080"


def test_expand_vars_missing_key_yields_empty() -> None:
    result = _expand_vars("${missing_key}", {})
    assert result == ""


def test_expand_vars_no_placeholders_returns_unchanged() -> None:
    result = _expand_vars("http://plain.example.com", {})
    assert result == "http://plain.example.com"


def test_expand_vars_multiple_placeholders() -> None:
    result = _expand_vars("${backend_server_url}:${baas_server_url}", _VARS)
    assert result == "http://backend:8080:http://baas:9090"


def test_parse_servers_with_valid_placeholder() -> None:
    servers = _parse_servers({"backend": {"base_url": "${backend_server_url}"}}, _VARS)
    assert servers["backend"].name == "backend"
    assert servers["backend"].base_url == "http://backend:8080"


def test_parse_servers_hardcoded_url() -> None:
    servers = _parse_servers(
        {"backend": {"base_url": "http://hardcoded.example.com"}}, {}
    )
    assert servers["backend"].base_url == "http://hardcoded.example.com"


def test_parse_servers_empty_base_url_raises() -> None:
    with pytest.raises(ValueError, match="resolved to empty") as excinfo:
        _parse_servers({"backend": {"base_url": "${backend_server_url}"}}, {})
    assert "backend" in str(excinfo.value)
    assert "backend_server_url" in str(excinfo.value)


def test_parse_servers_one_empty_one_valid_raises_for_empty() -> None:
    with pytest.raises(ValueError, match="baas"):
        _parse_servers(
            {
                "backend": {"base_url": "http://ok.example.com"},
                "baas": {"base_url": "${baas_server_url}"},
            },
            {},
        )


def test_parse_servers_empty_string_base_url_raises() -> None:
    with pytest.raises(ValueError, match="resolved to empty"):
        _parse_servers({"backend": {"base_url": ""}}, {})


def test_from_config_with_variables_resolves_all() -> None:
    dm = DomainMap.from_config(
        {
            "domains": {
                "bots": {"server": "backend"},
                "runs": {"server": "baas"},
            },
            "servers": {
                "backend": {"base_url": "${backend_server_url}"},
                "baas": {"base_url": "${baas_server_url}"},
            },
        },
        variables=_VARS,
    )
    bots = dm.resolve("/openapi/v1/bots")
    assert bots is not None
    assert bots.base_url == "http://backend:8080"
    runs = dm.resolve("/openapi/v1/runs")
    assert runs is not None
    assert runs.base_url == "http://baas:9090"


def test_from_config_missing_variable_raises() -> None:
    with pytest.raises(ValueError, match="backend_server_url"):
        DomainMap.from_config(
            {
                "domains": {"bots": {"server": "backend"}},
                "servers": {"backend": {"base_url": "${backend_server_url}"}},
            },
            variables={"baas_server_url": "http://baas:9090"},
        )


def test_from_yaml_loads_upstreams(tmp_path) -> None:
    cfg = tmp_path / "application.yaml"
    cfg.write_text(
        "user_config:\n"
        "  upstreams:\n"
        "    base_path: /openapi/v1\n"
        "    domains:\n"
        "      bots:\n"
        "        server: backend\n"
        "        schema:\n"
        "          source: file\n"
        "          path: schemas/bots.openapi.json\n"
        "    servers:\n"
        "      backend:\n"
        "        base_url: http://backend:8080\n"
    )
    dm = DomainMap.from_yaml(cfg, variables={})
    assert dm.resolve("/openapi/v1/bots/123") is not None


def test_from_yaml_non_dict_root_uses_empty(tmp_path) -> None:
    cfg = tmp_path / "application.yaml"
    cfg.write_text("- just a list")
    dm = DomainMap.from_yaml(cfg, variables={})
    assert dm.resolve("/openapi/v1/bots/123") is None


def test_from_yaml_user_config_not_dict_uses_empty(tmp_path) -> None:
    cfg = tmp_path / "application.yaml"
    cfg.write_text("user_config: not-a-dict\n")
    dm = DomainMap.from_yaml(cfg, variables={})
    assert dm.resolve("/openapi/v1/bots/123") is None
