"""The served OpenAPI is generated from the published description (parity)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from gateway.community.adapters.web.app import create_app

_SEED = (
    Path(__file__).resolve().parents[1] / "configs" / "schemas" / "bots.openapi.json"
)
_METHODS = {"get", "post", "put", "delete", "patch"}


def _ops(spec: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (path, method)
        for path, item in spec.get("paths", {}).items()
        for method in item
        if method in _METHODS
    }


def _served() -> dict[str, Any]:
    return TestClient(create_app()).get("/openapi.json").json()


def test_served_doc_is_superset_of_published_ops() -> None:
    seed = json.loads(_SEED.read_text())
    missing = _ops(seed) - _ops(_served())
    assert not missing, f"served doc dropped published operations: {sorted(missing)}"


def test_every_served_operation_carries_security() -> None:
    served = _served()
    for path, item in served["paths"].items():
        for method, operation in item.items():
            if method in _METHODS:
                assert "x-avernet-security" in operation, f"{method} {path}"


def test_served_doc_only_exposes_public_namespace() -> None:
    for path in _served()["paths"]:
        assert path.startswith("/openapi/v1"), path
