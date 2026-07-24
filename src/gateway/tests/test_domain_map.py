"""Unit tests for the domain → server map (config parsing + resolution)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.community.core.forwarding import DomainMap

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "upstreams.yaml"


def _map() -> DomainMap:
    return DomainMap.from_config(
        {
            "base_path": "/openapi/v1",
            "domains": {
                "bots": {
                    "server": "agentclaw",
                    "schema": {"source": "file", "path": "schemas/bots.openapi.json"},
                }
            },
            "servers": {"agentclaw": {"base_url": "http://backend:8080"}},
        }
    )


def test_resolves_configured_domain() -> None:
    server = _map().resolve("/openapi/v1/bots/123/restart")
    assert server is not None
    assert server.name == "agentclaw"
    assert server.base_url == "http://backend:8080"


def test_domain_at_root_resolves() -> None:
    assert _map().resolve("/openapi/v1/bots") is not None


def test_unknown_domain_returns_none() -> None:
    assert _map().resolve("/openapi/v1/unknown/x") is None


def test_path_outside_version_base_returns_none() -> None:
    assert _map().resolve("/api/bots/123") is None


def test_bare_version_base_has_no_domain() -> None:
    assert _map().resolve("/openapi/v1") is None


def test_env_var_base_url_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTCLAW_URL", "https://agentclaw.example.com")
    dm = DomainMap.from_config(
        {
            "domains": {"bots": {"server": "agentclaw"}},
            "servers": {"agentclaw": {"base_url": "${AGENTCLAW_URL}"}},
        }
    )
    server = dm.resolve("/openapi/v1/bots")
    assert server is not None
    assert server.base_url == "https://agentclaw.example.com"


def test_unknown_server_reference_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown server"):
        DomainMap.from_config({"domains": {"bots": {"server": "ghost"}}, "servers": {}})


def test_schema_source_parsed() -> None:
    domain = _map().domain_for("/openapi/v1/bots/1")
    assert domain is not None
    assert domain.schema.source == "file"
    assert domain.schema.location == "schemas/bots.openapi.json"
    assert domain.schema.refresh_seconds == 300


def test_shipped_config_loads() -> None:
    dm = DomainMap.from_yaml(_CONFIG)
    assert dm.domain_for("/openapi/v1/bots") is not None
