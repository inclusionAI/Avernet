"""Approved BCN internal OpenAPI V1 contract inventory."""

import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest

BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import (  # noqa: E402
    load_contract,
    validate_contract,
)

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

TEAM_MANAGER_SOURCES_PATH = "/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}"
TEAM_MANAGER_MEMBERS_PATH = (
    "/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}/members"
)

EXPECTED_OPERATIONS = {
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
    # Task 13 team-manager sources slice (spec §6.1): the PUT snapshot lane
    # plus the internal single-member repair endpoints, all under the
    # credential-bound team slice outside the collaboration prefix.
    ("put", TEAM_MANAGER_SOURCES_PATH),
    ("post", TEAM_MANAGER_MEMBERS_PATH),
    ("delete", TEAM_MANAGER_MEMBERS_PATH),
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
        or (method, path)
        in {
            ("put", TEAM_MANAGER_SOURCES_PATH),
            ("post", TEAM_MANAGER_MEMBERS_PATH),
            ("delete", TEAM_MANAGER_MEMBERS_PATH),
        }
        for method, path in _actual_operations()
    )


def test_contract_excludes_public_openapi_routes() -> None:
    actual = _actual_operations()
    assert not any(path.startswith("/openapi/v1/") for _, path in actual)


# ---------------------------------------------------------------------------
# Task 13: the team-manager sources slice (spec §6.1)
# ---------------------------------------------------------------------------


def _internal_contract():
    return load_contract(CONTRACT_ROOT, entrypoint="internal.yaml")


def _envelope(data):
    return {"code": 20000, "message": "OK", "data": data, "request_id": "req-001"}


def test_team_manager_slice_is_discovered_on_real_paths_only() -> None:
    actual = _actual_operations()
    # Discovery: the three approved team operations exist, and the team
    # slice never regresses into the collaboration prefix.
    assert ("put", TEAM_MANAGER_SOURCES_PATH) in actual
    assert ("post", TEAM_MANAGER_MEMBERS_PATH) in actual
    assert ("delete", TEAM_MANAGER_MEMBERS_PATH) in actual
    assert all(
        path.startswith("/api/v1/bots/") for _, path in actual if "/manager-sources/" in path
    )
    # The team slice is NOT mirrored into the public contract.
    public = load_contract(CONTRACT_ROOT)
    for _, path in {
        ("put", TEAM_MANAGER_SOURCES_PATH),
        ("post", TEAM_MANAGER_MEMBERS_PATH),
        ("delete", TEAM_MANAGER_MEMBERS_PATH),
    }:
        assert path not in public["paths"]
        assert path.replace("/api/", "/openapi/") not in public["paths"]


def test_validator_accepts_the_real_team_slice_without_widening_the_prefix() -> None:
    contract = _internal_contract()
    errors = validate_contract(contract, path_prefix="/api/v1/collaboration/")
    assert errors == []


def _minimal_operation(operation_id: str) -> dict:
    return {
        "operationId": operation_id,
        "x-avernet-security": {},
        "responses": {
            "200": {
                "description": "ok",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["code", "message", "data", "request_id"],
                        }
                    }
                },
            }
        },
    }


def _contract_with_extra_path(method: str, path: str):
    contract = _internal_contract()
    mutated = copy.deepcopy(contract)
    mutated["paths"][path] = {method: _minimal_operation("extra_operation")}
    return mutated


@pytest.mark.parametrize(
    "method, path",
    [
        # Arbitrary /api/v1/* widening is forbidden.
        ("put", "/api/v1/bots/{bot_id}"),
        ("put", "/api/v1/bots/{bot_id}/manager-sources"),
        ("put", "/api/v1/bots/{bot_id}/manager-sources/teams"),
        ("post", "/api/v1/others"),
        # Wrong METHOD on an approved team template is not allowed.
        ("get", TEAM_MANAGER_SOURCES_PATH),
        ("get", TEAM_MANAGER_MEMBERS_PATH),
        ("post", TEAM_MANAGER_SOURCES_PATH),
        ("delete", TEAM_MANAGER_SOURCES_PATH),
        # Wrong TEMPLATE shape for an approved method is not allowed.
        ("put", "/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}/members"),
        ("post", "/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}/members/{user_id}"),
        (
            "delete",
            "/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}/{user_id}",
        ),
    ],
)
def test_validator_rejects_unapproved_api_v1_widenings(method: str, path: str) -> None:
    contract = _contract_with_extra_path(method, path)
    errors = validate_contract(contract, path_prefix="/api/v1/collaboration/")
    assert any("path is outside" in error for error in errors), errors


def test_team_manager_put_body_schema_is_precise() -> None:
    contract = _internal_contract()
    operation = contract["paths"][TEAM_MANAGER_SOURCES_PATH]["put"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    envelope_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]

    # The body is a oneOf over the two operation kinds: every branch is
    # additionalProperties:false and requires the three-field core
    # (move adds new_team_id; sync forbids it).
    assert set(schema["oneOf"][0]["required"]) == {
        "operation",
        "manager_user_ids",
        "idempotency_key",
    }
    assert schema["oneOf"][0]["additionalProperties"] is False
    assert set(schema["oneOf"][1]["required"]) == {
        "operation",
        "new_team_id",
        "manager_user_ids",
        "idempotency_key",
    }
    assert schema["oneOf"][1]["additionalProperties"] is False
    for branch in schema["oneOf"]:
        assert branch["properties"]["idempotency_key"]["minLength"] == 1
        items = branch["properties"]["manager_user_ids"]["items"]
        assert items["type"] == "string"
        assert items["minLength"] == 1
        # An empty snapshot IS legal (full revoke of this team's source).
        assert branch["properties"]["manager_user_ids"].get("minItems") is None

    # Hit: a legal sync envelope validates.
    jsonschema.validate(
        _envelope(
            {
                "operation_id": "op-1",
                "bot_id": "bot-a",
                "team_id": "team-a",
                "operation": "sync",
                "granted_count": 2,
                "revoked_count": 1,
            }
        ),
        envelope_schema,
    )


def test_team_manager_body_negatives_are_enforced_by_jsonschema() -> None:
    contract = _internal_contract()
    operation = contract["paths"][TEAM_MANAGER_SOURCES_PATH]["put"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]

    bad_bodies = [
        # missing manager_user_ids
        {"operation": "sync", "idempotency_key": "k"},
        # missing operation
        {"manager_user_ids": [], "idempotency_key": "k"},
        # missing idempotency_key
        {"operation": "sync", "manager_user_ids": []},
        # unknown operation
        {"operation": "reconcile", "manager_user_ids": [], "idempotency_key": "k"},
        # unknown field
        {"operation": "sync", "manager_user_ids": [], "idempotency_key": "k", "foo": 1},
        # missing new_team_id on move
        {"operation": "move", "manager_user_ids": [], "idempotency_key": "k"},
        # sync carrying new_team_id is rejected by oneOf (accepts sync OR
        # move+new_team_id, never both -- enforced by the not-allOf trick
        # below through additionalProperties exclusion in the sync branch)
        {"operation": "sync", "new_team_id": "team-new", "manager_user_ids": [], "idempotency_key": "k"},
    ]
    for body in bad_bodies:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(body, schema)

    # Hit: legal sync and legal move both parse.
    jsonschema.validate(
        {"operation": "sync", "manager_user_ids": [], "idempotency_key": "k"}, schema
    )
    jsonschema.validate(
        {"operation": "sync", "manager_user_ids": ["user-a", "user-a"], "idempotency_key": "k"},
        schema,
    )
    jsonschema.validate(
        {
            "operation": "move",
            "new_team_id": "team-new",
            "manager_user_ids": ["user-a"],
            "idempotency_key": "k",
        },
        schema,
    )


def test_team_member_repair_contract_shapes() -> None:
    contract = _internal_contract()
    post = contract["paths"][TEAM_MANAGER_MEMBERS_PATH]["post"]
    delete = contract["paths"][TEAM_MANAGER_MEMBERS_PATH]["delete"]

    # POST body: user_id + idempotency_key only.
    body_schema = post["requestBody"]["content"]["application/json"]["schema"]
    assert set(body_schema["required"]) == {"user_id", "idempotency_key"}
    assert body_schema["additionalProperties"] is False
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"user_id": "user-b"}, body_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"user_id": "user-b", "idempotency_key": "k", "team": "team-a"}, body_schema
        )
    jsonschema.validate({"user_id": "user-b", "idempotency_key": "k"}, body_schema)

    # DELETE: query user_id + Idempotency-Key header.
    delete_params = {(item["in"], item["name"]) for item in delete["parameters"]}
    assert ("query", "user_id") in delete_params
    assert ("header", "Idempotency-Key") in delete_params
    required_params = {
        item["name"]
        for item in delete["parameters"]
        if item.get("required") is True
    }
    # The path identities carry over from the route; the ONLY repair
    # inputs are user_id and the idempotency header, both required.
    assert {"user_id", "Idempotency-Key"} <= required_params <= {
        "user_id",
        "Idempotency-Key",
        "bot_id",
        "team_id",
    }


def test_team_operations_declare_the_service_credential_boundary() -> None:
    contract = _internal_contract()
    for method, path in [
        ("put", TEAM_MANAGER_SOURCES_PATH),
        ("post", TEAM_MANAGER_MEMBERS_PATH),
        ("delete", TEAM_MANAGER_MEMBERS_PATH),
    ]:
        operation = contract["paths"][path][method]
        # No Human/App/Bot Principal requirements: the verified service
        # credential is a separate security declaration.
        assert operation["security"] == [{"TeamManagerServiceCredential": []}]
        scheme = contract["components"]["securitySchemes"]["TeamManagerServiceCredential"]
        assert scheme["scheme"] == "bearer"