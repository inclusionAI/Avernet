"""Approved BCN internal OpenAPI V1 contract inventory."""

import sys
from pathlib import Path

BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract  # noqa: E402

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

EXPECTED_OPERATIONS = {
    ("get", "/api/v1/collaboration/agents/me"),
    ("get", "/api/v1/collaboration/drafts/{draftId}/revisions/{revision}"),
    ("post", "/api/v1/collaboration/drafts/{draftId}/revisions/{revision}/validate"),
    ("get", "/api/v1/collaboration/providers/by-slug/{slug}"),
    ("get", "/api/v1/collaboration/bots/me"),
    ("get", "/api/v1/collaboration/bots/{bot_id}/candidates/search"),
    ("get", "/api/v1/collaboration/templates"),
    ("get", "/api/v1/collaboration/templates/{template_id}"),
    ("post", "/api/v1/collaboration/definitions/validate"),
    ("get", "/api/v1/collaboration/manifest"),
    ("get", "/api/v1/collaboration/assets/{bundle_name}/{file_name}"),
    ("get", "/api/v1/collaboration/state-machine-runs/{run_id}"),
    ("get", "/api/v1/collaboration/state-machine-runs/{run_id}/graph"),
    ("get", "/api/v1/collaboration/state-machine-runs/{run_id}/nodes/{node_id}"),
    ("get", "/api/v1/collaboration/state-machine-runs/{run_id}/pending-human-nodes"),
    ("post", "/api/v1/collaboration/state-machine-runs/{run_id}/nodes/{node_id}/respond"),
    ("post", "/api/v1/collaboration/state-machine-runs/{run_id}/reruns"),
    ("post", "/api/v1/collaboration/state-machine-runs/{run_id}/cancel"),
    ("get", "/api/v1/collaboration/sessions/{session_id}/files"),
    ("post", "/api/v1/collaboration/sessions/{session_id}/files"),
    ("get", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}"),
    ("delete", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}"),
    ("get", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/content"),
    ("put", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/content"),
    ("post", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/complete"),
    ("post", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/share"),
    ("get", "/api/v1/collaboration/sessions/shared-file/content"),
    # Bot authority surface (spec §5.1/§6.1) — one unified
    # route group; Human and trusted-App callers share it.
    ("get", "/api/v1/collaboration/bots/{bot_id}/managers"),
    ("put", "/api/v1/collaboration/bots/{bot_id}/managers"),
    ("put", "/api/v1/collaboration/bots/{bot_id}/managers/{user_id}"),
    ("delete", "/api/v1/collaboration/bots/{bot_id}/managers/{user_id}"),
    ("get", "/api/v1/collaboration/bots/{bot_id}/ownership"),
    (
        "put",
        "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}",
    ),
    (
        "get",
        "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}",
    ),
    (
        "post",
        "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/accept",
    ),
    (
        "post",
        "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/reject",
    ),
    (
        "post",
        "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/cancel",
    ),
    ("get", "/api/v1/collaboration/ownership-transfers"),
}


def _actual_operations():
    contract = load_contract(CONTRACT_ROOT, entrypoint="internal.yaml")
    return {
        (method, path)
        for path, path_item in contract["paths"].items()
        for method in path_item
        if method.lower() in HTTP_METHODS
    }


def test_contract_contains_exactly_the_approved_internal_operations() -> None:
    assert _actual_operations() == EXPECTED_OPERATIONS


def test_all_operations_share_the_internal_ownership_prefix() -> None:
    assert all(
        path.startswith("/api/v1/collaboration/")
        for _, path in _actual_operations()
    )


def test_contract_excludes_public_openapi_routes() -> None:
    actual = _actual_operations()
    assert not any(path.startswith("/openapi/v1/") for _, path in actual)


def test_bot_authority_operations_carry_frozen_contract_ids() -> None:
    contract = load_contract(CONTRACT_ROOT, entrypoint="internal.yaml")
    methods = {"get", "post", "put", "delete"}
    operation_ids = {
        path: [item[m]["operationId"] for m in item if m in methods]
        for path, item in contract["paths"].items()
        if path in AUTHORITY_PATHS
    }
    flat_ids = {
        operation_id
        for ids in operation_ids.values()
        for operation_id in ids
    }

    # The plan records the collection-PUT operation id as
    # `syncBotManagers`; the repository conformance gate
    # (`scripts/validate_openapi_contract.py`) enforces
    # snake_case, so the frozen contract id is
    # `sync_bot_managers`.
    assert (
        "sync_bot_managers"
        in operation_ids["/api/v1/collaboration/bots/{bot_id}/managers"]
    )

    # One unified path group only (no second Human/App URL
    # group) and exactly 11 frozen verb/path combos.
    assert len(operation_ids) == 8
    assert sum(1 for m, p in EXPECTED_OPERATIONS if p in AUTHORITY_PATHS) == 11
    # Unique ids across the surface.
    assert len(flat_ids) == sum(len(ids) for ids in operation_ids.values())


AUTHORITY_PATHS = {
    path for (_, path) in EXPECTED_OPERATIONS
    if "managers" in path or "ownership" in path
}
