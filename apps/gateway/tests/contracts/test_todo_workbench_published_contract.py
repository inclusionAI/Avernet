"""Published Gateway contract for the human Todo workbench."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from gateway.community.core.authn import RouteSecurity
from gateway.community.core.forwarding import DomainMap
from gateway.community.spi.authn import Presence, PrincipalType

_GATEWAY = Path(__file__).resolve().parents[2]
_ARTIFACT = _GATEWAY / "configs" / "schemas" / "bots.openapi.json"
_CONFIG = _GATEWAY / "configs" / "application.yaml"
_VARS = {
    "backend_server_url": "http://backend:8080",
    "baas_server_url": "http://baas:9090",
    "bcs_server_url": "http://bcs:8081",
    "engine_proxy_server_url": "https://engineproxy:20003",
    "bcsfuse_server_url": "http://bcsfuse:8765",
    "clawweb_server_url": "http://clawweb:8082",
}
_EXPECTED_OPERATIONS = {
    "/openapi/v1/todos": {"get", "post"},
    "/openapi/v1/todos/{todo_id}": {"get", "patch"},
    "/openapi/v1/todos/{todo_id}/run": {"post"},
    "/openapi/v1/todos/{todo_id}/events": {"get"},
    "/openapi/v1/todos/{todo_id}/interactions": {"get", "post"},
    "/openapi/v1/todos/{todo_id}/acceptance": {"post"},
}


def test_todo_routes_and_methods_are_published() -> None:
    document = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    for path, methods in _EXPECTED_OPERATIONS.items():
        operation = document["paths"][path]
        assert set(operation) == methods, path
        for method in methods:
            assert operation[method]["responses"]["200"] is not None, (
                f"{method.upper()} {path}"
            )


def test_todo_domain_forwards_verbatim_and_requires_user() -> None:
    raw = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
    domains = DomainMap.from_config(raw["user_config"]["upstreams"], variables=_VARS)
    security = RouteSecurity.from_table(raw["user_config"]["route_security"])

    for path, methods in _EXPECTED_OPERATIONS.items():
        domain = domains.http_domain_for(path)
        assert domain is not None, path
        assert domain.name == "todos", path
        assert domain.server.name == "backend", path
        assert domain.upstream_path(path) == path, path
        assert domain.schema.location == "schemas/bots.openapi.json", path

        for method in methods:
            requirement = security.resolve(method.upper(), path)
            assert requirement is not None, f"{method.upper()} {path}"
            assert requirement[PrincipalType.USER] == Presence.REQUIRED, (
                f"{method.upper()} {path}"
            )
