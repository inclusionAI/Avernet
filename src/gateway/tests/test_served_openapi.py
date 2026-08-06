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
_BAAS_ARTIFACT = (
    Path(__file__).resolve().parents[1] / "configs" / "schemas" / "baas.openapi.json"
)
_BCN_ARTIFACT = (
    Path(__file__).resolve().parents[1] / "configs" / "schemas" / "bcn.openapi.json"
)
_SHIPPED_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "application.yaml"
_METHODS = {"get", "post", "put", "delete", "patch"}
_RULES = RouteSecurity.from_table({"/**": {"user": "required"}})
_SHIPPED_RULES = RouteSecurity.from_yaml(_SHIPPED_CONFIG)


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
                assert operation["x-avernet-security"] == {"user": "required"}, (
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


def test_top_level_tags_are_merged_once_in_domain_order() -> None:
    descriptions = {
        "first": {
            "tags": [{"name": "Collaboration / Bots", "description": "Bot resources."}],
            "paths": {},
        },
        "second": {
            "tags": [
                {"name": "Collaboration / Bots", "description": "Bot resources."},
                {"name": "Collaboration / Groups", "description": "Group resources."},
            ],
            "paths": {},
        },
    }

    document = build_served_openapi(
        ["first", "second"],
        descriptions.__getitem__,
        _RULES,
        title="gateway",
        version="0.1.0",
    )

    assert document["tags"] == [
        {"name": "Collaboration / Bots", "description": "Bot resources."},
        {"name": "Collaboration / Groups", "description": "Group resources."},
    ]


def test_served_openapi_aggregates_bcn_with_existing_domains() -> None:
    descriptions = {
        "bots": json.loads(_FIXTURE.read_text()),
        "chat": json.loads(_BAAS_ARTIFACT.read_text()),
        "collaboration": json.loads(_BCN_ARTIFACT.read_text()),
    }
    document = build_served_openapi(
        ["bots", "chat", "collaboration"],
        descriptions.__getitem__,
        _SHIPPED_RULES,
        title="gateway",
        version="0.1.0",
    )

    paths = document["paths"]
    assert "/openapi/v1/bots" in paths
    # REL #748 renamed the BaaS chat/session surface to /openapi/v1/chat/**;
    # the shipped baas artifact now serves the sessions path under chat.
    assert "/openapi/v1/chat/sessions/{session_id}" in paths
    assert "/openapi/v1/collaboration/bots/mine" in paths
    assert "post" in paths["/openapi/v1/collaboration/sessions/{session_id}/token"]
    assert "get" in paths["/openapi/v1/collaboration/messages/ws"]
    assert paths["/openapi/v1/collaboration/bots/mine"]["get"][
        "x-avernet-security"
    ] == {"user": "required"}
    # REL qualified the collaboration messages/ws exemption by plane: only the
    # WEBSOCKET handshake is exempt (BCN verifies its session credential); the
    # HTTP GET operation on the same path keeps the user requirement.
    assert paths["/openapi/v1/collaboration/messages/ws"]["get"][
        "x-avernet-security"
    ] == {"user": "required"}
    assert paths["/openapi/v1/collaboration/sessions/{session_id}/token"]["post"][
        "tags"
    ] == ["Collaboration / Sessions"]
    assert paths["/openapi/v1/collaboration/messages/ws"]["get"]["tags"] == [
        "Collaboration / Sessions"
    ]
    assert [tag["name"] for tag in document["tags"]] == [
        "Collaboration / Bots",
        "Collaboration / Friendships",
        "Collaboration / Groups",
        "Collaboration / Sessions",
        "Collaboration / Invitations",
    ]


_BCSFUSE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bcsfuse.openapi.json"


def _bcsfuse_served() -> dict[str, Any]:
    description = json.loads(_BCSFUSE_FIXTURE.read_text())
    return build_served_openapi(
        ["bcsfuse"],
        lambda _domain: description,
        _SHIPPED_RULES,
        title="gateway",
        version="0.1.0",
        description="test",
    )


def test_bcsfuse_paths_served_with_user_security() -> None:
    paths = _bcsfuse_served()["paths"]
    assert set(paths) == {
        "/openapi/v1/bcsfuse/api/v1/groups/{group_id}/fuse",
        "/openapi/v1/bcsfuse/v1/workers/{worker_id}/config",
        "/openapi/v1/bcsfuse/v1/workers/config/batch",
    }
    for path, item in paths.items():
        for method, operation in item.items():
            if method in _METHODS:
                assert operation["x-avernet-security"] == {"user": "required"}, (
                    f"{method} {path}"
                )
