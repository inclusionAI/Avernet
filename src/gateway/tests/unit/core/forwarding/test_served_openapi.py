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
from gateway.community.core.forwarding import (
    DomainMap,
    build_combined_openapi,
    build_served_openapi,
)

_FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "bots.openapi.json"
_BAAS_ARTIFACT = (
    Path(__file__).resolve().parents[4] / "configs" / "schemas" / "baas.openapi.json"
)
_BCN_ARTIFACT = (
    Path(__file__).resolve().parents[4] / "configs" / "schemas" / "bcn.openapi.json"
)
_BCN_INTERNAL_ARTIFACT = (
    Path(__file__).resolve().parents[4]
    / "configs"
    / "schemas"
    / "bcn.internal.openapi.json"
)
_SHIPPED_CONFIG = Path(__file__).resolve().parents[4] / "configs" / "application.yaml"
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
    assert not any(path.startswith("/api/v1/collaboration") for path in paths)
    assert "post" in paths["/openapi/v1/collaboration/sessions/{session_id}/token"]
    collection = paths["/openapi/v1/collaboration/sessions/{session_id}/collect"]
    assert set(collection) == {"delete", "post"}
    assert "get" in paths["/openapi/v1/collaboration/messages/ws"]
    message_data = paths["/openapi/v1/collaboration/sessions/{session_id}/messages"][
        "get"
    ]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["data"]
    assert message_data["type"] == "array"
    assert {
        "historyMeta",
        "metadata",
        "attachments",
        "role",
        "bot_name",
        "run_id",
    }.issubset(message_data["items"]["properties"])
    assert {"messages", "next_cursor", "has_more"}.isdisjoint(message_data)
    collaboration_http_security = {"user": "required", "app": "required"}
    assert collection["post"]["x-avernet-security"] == collaboration_http_security
    assert collection["delete"]["x-avernet-security"] == collaboration_http_security
    assert (
        paths["/openapi/v1/collaboration/bots/mine"]["get"]["x-avernet-security"]
        == collaboration_http_security
    )
    assert (
        paths["/openapi/v1/collaboration/sessions/{session_id}/token"]["post"][
            "x-avernet-security"
        ]
        == collaboration_http_security
    )
    # REL qualified the collaboration messages/ws exemption by plane: only the
    # WEBSOCKET handshake is exempt (BCN verifies its session credential); the
    # HTTP GET operation on the same path keeps the collaboration HTTP security.
    assert (
        paths["/openapi/v1/collaboration/messages/ws"]["get"]["x-avernet-security"]
        == collaboration_http_security
    )
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
        "Collaboration / Channels",
    ]


def test_shipped_internal_openapi_serves_bcn_internal_paths_only() -> None:
    internal = json.loads(_BCN_INTERNAL_ARTIFACT.read_text())
    public = json.loads(_BCN_ARTIFACT.read_text())

    document = build_combined_openapi(
        ["bcn-internal"],
        {"bcn-internal": internal, "collaboration": public}.__getitem__,
        title="gateway internal",
        version="0.1.0",
        description="Internal APIs",
    )

    paths = document["paths"]
    assert "/api/v1/collaboration/sessions/{session_id}/files" in paths
    assert "/api/v1/collaboration/bots/{bot_id}/candidates/search" in paths
    assert not any(path.startswith("/openapi/v1/collaboration") for path in paths)


def test_served_internal_openapi_combines_only_internal_schema_paths() -> None:
    internal = {
        "openapi": "3.1.0",
        "info": {"title": "BCN Internal", "version": "1.0.0"},
        "paths": {
            "/api/v1/collaboration/sessions/{session_id}/files": {
                "get": {
                    "tags": ["Internal / Session Files"],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "components": {"schemas": {"InternalFile": {"type": "object"}}},
        "tags": [
            "not-a-tag",
            {"name": "Internal / Session Files"},
            {"name": "Internal / Session Files", "description": "duplicate"},
            {"name": 123},
        ],
    }
    public = {
        "openapi": "3.1.0",
        "info": {"title": "BCN Public", "version": "1.0.0"},
        "paths": {
            "/openapi/v1/collaboration/groups": {
                "get": {
                    "tags": ["Collaboration / Groups"],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    document = build_combined_openapi(
        ["bcn-internal"],
        {"bcn-internal": internal, "bcn": public}.__getitem__,
        title="gateway internal",
        version="0.1.0",
        description="Internal APIs",
    )

    assert document["info"]["title"] == "gateway internal"
    assert "/api/v1/collaboration/sessions/{session_id}/files" in document["paths"]
    assert "/openapi/v1/collaboration/groups" not in document["paths"]
    assert document["components"]["schemas"]["InternalFile"] == {"type": "object"}
    assert document["tags"] == [{"name": "Internal / Session Files"}]


_BCSFUSE_FUSION_FIXTURE = (
    Path(__file__).resolve().parents[3] / "fixtures" / "bcsfuse-fusion.openapi.json"
)
_BCSFUSE_WORKERS_FIXTURE = (
    Path(__file__).resolve().parents[3] / "fixtures" / "bcsfuse-workers.openapi.json"
)

# Every server in the shipped config is templated as ${...}_server_url, so the
# catalog needs a value for each to build the domain map.
_BCSFUSE_VARS = {
    "backend_server_url": "http://backend:8080",
    "baas_server_url": "http://baas:9090",
    "bcs_server_url": "http://bcs:8081",
    "engine_proxy_server_url": "https://engineproxy:20003",
    "bcsfuse_server_url": "http://bcsfuse:8765",
}


def _bcsfuse_served() -> dict[str, Any]:
    # The clean bcsfuse surface is two matched-child domains whose mount prefixes
    # are not /openapi/v1/<name>; pass the real mount prefixes so their paths
    # survive the served-doc namespace filter.
    dm = DomainMap.from_yaml(_SHIPPED_CONFIG, variables=_BCSFUSE_VARS)
    mount_prefixes = {name: domain.mount_prefix for name, domain in dm.domains.items()}
    rewrites = {name: domain.rewrite for name, domain in dm.domains.items()}
    describe = {
        "bcsfuse-fusion": json.loads(_BCSFUSE_FUSION_FIXTURE.read_text()),
        "bcsfuse-workers": json.loads(_BCSFUSE_WORKERS_FIXTURE.read_text()),
    }
    return build_served_openapi(
        ["bcsfuse-fusion", "bcsfuse-workers"],
        describe.__getitem__,
        _SHIPPED_RULES,
        title="gateway",
        version="0.1.0",
        description="test",
        rewrites=rewrites,
        mount_prefixes=mount_prefixes,
    )


def test_bcsfuse_paths_served_with_user_security() -> None:
    paths = _bcsfuse_served()["paths"]
    assert set(paths) == {
        "/openapi/v1/bcsfuse/groups/{group_id}/fuse",
        "/openapi/v1/bcsfuse/workers/{worker_id}/config",
        "/openapi/v1/bcsfuse/workers/config/batch",
    }
    for path, item in paths.items():
        for method, operation in item.items():
            if method in _METHODS:
                assert operation["x-avernet-security"] == {"user": "required"}, (
                    f"{method} {path}"
                )


_BOTS_ARTIFACT = (
    Path(__file__).resolve().parents[4]
    / "configs"
    / "schemas"
    / "bots.openapi.json"
)


def test_harness_paths_served_with_user_security() -> None:
    """The harness operations publish beneath the addressed bot.

    They live under ``/openapi/v1/bots/{bot_id}/harness/…`` now, so the bots
    domain routes and documents them: no separate domain can pin a match behind
    the ``{bot_id}`` parameter, and the bots artifact carries their description.
    Their rule is the one thing that stays their own — a user on the wire,
    outranking the wide optional rule the rest of the bots surface admits.
    """
    dm = DomainMap.from_yaml(_SHIPPED_CONFIG, variables=_BCSFUSE_VARS)
    mount_prefixes = {name: domain.mount_prefix for name, domain in dm.domains.items()}
    rewrites = {name: domain.rewrite for name, domain in dm.domains.items()}
    describe = {"bots": json.loads(_BOTS_ARTIFACT.read_text())}
    document = build_served_openapi(
        ["bots"],
        describe.__getitem__,
        _SHIPPED_RULES,
        title="gateway",
        version="0.1.0",
        description="test",
        rewrites=rewrites,
        mount_prefixes=mount_prefixes,
    )

    harness_paths = {
        "/openapi/v1/bots/{bot_id}/harness/diagnose",
        "/openapi/v1/bots/{bot_id}/harness/preview",
        "/openapi/v1/bots/{bot_id}/harness/apply",
        "/openapi/v1/bots/{bot_id}/harness/rollback",
        "/openapi/v1/bots/{bot_id}/harness/dim-report",
        "/openapi/v1/bots/{bot_id}/harness/dim-history",
    }
    assert harness_paths <= set(document["paths"])
    # The retired shape is published nowhere.
    assert not any(p.startswith("/openapi/v1/harness") for p in document["paths"])
    for path in harness_paths:
        for method, operation in document["paths"][path].items():
            if method in _METHODS:
                assert operation["x-avernet-security"] == {"user": "required"}, (
                    f"{method} {path}"
                )
