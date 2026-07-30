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
    dm = DomainMap.from_config(raw["upstreams"], variables=_VARS)
    assert dm.domain_for("/openapi/v1/bots") is not None


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
