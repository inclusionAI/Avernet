"""Contract + auth tests across all /openapi/v1 groups."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from gateway.community.adapters.web.app import create_app

_GROUP_PREFIXES = [
    "/openapi/v1/bots",
    "/openapi/v1/channels",
    "/openapi/v1/identity",
    "/openapi/v1/mcp",
    "/openapi/v1/resources",
    "/openapi/v1/routines",
    "/openapi/v1/skills",
]


def _openapi() -> dict[str, Any]:
    return TestClient(create_app()).get("/openapi.json").json()


@pytest.mark.parametrize("prefix", _GROUP_PREFIXES)
def test_group_is_mounted(prefix: str) -> None:
    paths = _openapi()["paths"]
    assert any(path.startswith(prefix) for path in paths), f"{prefix} not mounted"


def test_every_v1_operation_declares_security() -> None:
    paths = _openapi()["paths"]
    operations = [
        (path, method, op)
        for path, item in paths.items()
        if path.startswith("/openapi/v1/")
        for method, op in item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert operations
    for path, method, op in operations:
        assert "x-avernet-security" in op, f"{method} {path} missing x-avernet-security"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/openapi/v1/channels"),
        ("get", "/openapi/v1/identity/bot/b1"),
        ("get", "/openapi/v1/mcp/servers"),
        ("get", "/openapi/v1/resources"),
        ("get", "/openapi/v1/routines"),
        ("get", "/openapi/v1/skills"),
    ],
)
def test_group_endpoints_require_auth(method: str, path: str) -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    assert client.request(method, path).status_code == 401
