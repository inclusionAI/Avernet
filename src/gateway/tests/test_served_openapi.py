"""The served OpenAPI is generated from a domain's published description.

Driven by a small committed fixture rather than the real dumped artifact — the
artifact is a build output (produced by the backend's ``dump_openapi`` for the
single-box file, or pulled from the object store in the enterprise flavor) and
is not committed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gateway.community.core.authn import RouteSecurity
from gateway.community.core.forwarding import build_served_openapi

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bots.openapi.json"
_METHODS = {"get", "post", "put", "delete", "patch"}
_RULES = RouteSecurity.from_table({"/**": ["first_party_user"]})


def _served() -> dict[str, Any]:
    description = json.loads(_FIXTURE.read_text())
    return build_served_openapi(
        ["bots"],
        lambda _domain: description,
        _RULES,
        title="gateway",
        version="0.1.0",
        description="test",
    )


def test_public_operations_are_served() -> None:
    paths = _served()["paths"]
    assert set(paths) == {"/openapi/v1/bots", "/openapi/v1/bots/{id}"}
    assert set(paths["/openapi/v1/bots"]) >= {"get", "post"}


def test_non_public_paths_are_filtered_out() -> None:
    assert "/api/internal/debug" not in _served()["paths"]


def test_every_served_operation_carries_security() -> None:
    for path, item in _served()["paths"].items():
        for method, operation in item.items():
            if method in _METHODS:
                assert operation["x-avernet-security"] == [{"first_party_user": {}}], (
                    f"{method} {path}"
                )


def test_components_pruned_to_referenced() -> None:
    schemas = _served()["components"]["schemas"]
    # Bot (via get + BotList.items), BotList, BotCreate; Unused is dropped.
    assert set(schemas) == {"Bot", "BotList", "BotCreate"}


def test_empty_catalog_yields_empty_but_valid_doc() -> None:
    doc = build_served_openapi(
        ["bots"], lambda _d: {}, _RULES, title="gateway", version="0.1.0"
    )
    assert doc["openapi"].startswith("3.")
    assert doc["paths"] == {}
