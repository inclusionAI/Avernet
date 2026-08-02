"""Bot control-plane OpenAPI V1 contract decisions."""

import sys
from pathlib import Path

BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract  # noqa: E402


BOT_OPERATIONS = {
    ("get", "/openapi/v1/bots/{bot_id}/candidates"),
    ("post", "/openapi/v1/bots/query"),
    ("get", "/openapi/v1/bots/{bot_id}"),
    ("patch", "/openapi/v1/bots/{bot_id}"),
    ("get", "/openapi/v1/bots/mine"),
}


def _contract():
    return load_contract(CONTRACT_ROOT)


def _operation(method: str, path: str):
    return _contract()["paths"][path][method]


def _parameters(operation):
    return {parameter["name"]: parameter for parameter in operation.get("parameters", [])}


def _success_data(operation, status: str = "200"):
    return operation["responses"][status]["content"]["application/json"]["schema"][
        "properties"
    ]["data"]


def test_bot_management_operations_are_human_control_plane_only() -> None:
    contract = _contract()

    for method, path in BOT_OPERATIONS:
        operation = contract["paths"][path][method]
        assert operation["x-avernet-security"] == {"principal": "human"}


def test_bot_domain_model_is_a_strict_bot_human_union() -> None:
    bot = _contract()["components"]["schemas"]["Bot"]
    variants = {
        variant["properties"]["kind"]["const"]: variant for variant in bot["oneOf"]
    }

    assert bot["discriminator"]["propertyName"] == "kind"
    assert set(bot["discriminator"]["mapping"]) == {"bot", "human"}
    assert set(variants) == {"bot", "human"}

    common_required = {
        "bot_id",
        "kind",
        "name",
        "visibility",
        "status",
        "env",
        "created_at",
        "updated_at",
    }
    physical = variants["bot"]
    human = variants["human"]

    assert physical["additionalProperties"] is False
    assert human["additionalProperties"] is False
    assert set(physical["required"]) == common_required | {"descriptor", "reachability"}
    assert set(human["required"]) == common_required
    assert "created_by" in physical["properties"]
    assert "created_by" in human["properties"]
    assert "created_by" not in physical["required"]
    assert "created_by" not in human["required"]

    physical_only = {"descriptor", "reachability", "provider", "agent_code"}
    assert physical_only.issubset(physical["properties"])
    assert physical_only.isdisjoint(human["properties"])
    assert set(physical["properties"]["provider"]["required"]) == {
        "provider_id",
        "name",
    }
    assert "slug" not in physical["properties"]["provider"]["properties"]

    descriptor = physical["properties"]["descriptor"]
    assert set(descriptor["required"]) == {"summary", "domains", "skills", "scopes"}
    assert set(descriptor["properties"]) == {"summary", "domains", "skills", "scopes"}
    assert set(descriptor["properties"]["skills"]["items"]["required"]) == {"name"}

    assert set(physical["properties"]["visibility"]["enum"]) == {
        "public",
        "protected",
        "private",
    }
    assert set(physical["properties"]["status"]["enum"]) == {"online", "hidden"}
    assert set(physical["properties"]["reachability"]["enum"]) == {
        "reachable",
        "unreachable",
    }
    assert "gmt_create" in physical["properties"]["created_at"]["description"]
    assert "gmt_modified" in physical["properties"]["updated_at"]["description"]

    serialized = repr(bot)
    for secret in ("session_token", "agent_token", "access_token", "cookie"):
        assert secret not in serialized


def test_candidates_contract_matches_legacy_list_semantics() -> None:
    operation = _operation("get", "/openapi/v1/bots/{bot_id}/candidates")
    parameters = _parameters(operation)

    assert set(parameters) == {"bot_id", "purpose", "name", "offset", "limit"}
    assert parameters["bot_id"]["in"] == "path"
    assert parameters["bot_id"]["required"] is True
    assert parameters["purpose"]["schema"] == {
        "type": "string",
        "enum": ["discovery", "collaboration"],
        "default": "discovery",
    }
    assert parameters["offset"]["schema"]["default"] == 0
    assert parameters["limit"]["schema"]["default"] == 20
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert "acting_bot_id" not in parameters

    assert operation["x-avernet-behavior"] == {
        "acting_bot": "managed_physical_bot",
        "result_kind": "bot",
        "environment": "same_as_acting_bot",
        "exclude_self": True,
        "purpose_visibility": {
            "discovery": ["public", "protected"],
            "collaboration": ["public", "friend"],
        },
        "status_filter": "none",
        "reachability_filter": "none",
        "ordering": ["created_at_desc", "bot_id_asc"],
    }

    candidate = _success_data(operation)["properties"]["items"]["items"]
    assert set(candidate["required"]) == {"bot", "is_friend"}
    assert candidate["properties"]["bot"]["properties"]["kind"]["const"] == "bot"


def test_batch_query_is_sparse_ordered_and_not_visibility_filtered() -> None:
    operation = _operation("post", "/openapi/v1/bots/query")
    request = operation["requestBody"]["content"]["application/json"]["schema"]
    bot_ids = request["properties"]["bot_ids"]

    assert request["additionalProperties"] is False
    assert request["required"] == ["bot_ids"]
    assert bot_ids["maxItems"] == 100
    assert bot_ids.get("minItems", 0) == 0
    assert bot_ids.get("uniqueItems", False) is False
    assert operation["x-avernet-behavior"] == {
        "result_kinds": ["bot", "human"],
        "authorization_filter": "none",
        "visibility_filter": "none",
        "deduplicate": "first_occurrence",
        "ordering": "request_order",
        "missing": "omit",
        "deleted": "omit",
        "unonboarded": "omit",
    }

    data = _success_data(operation)
    assert data["required"] == ["items"]
    assert data["properties"]["items"]["items"]["discriminator"]["propertyName"] == "kind"
    assert "404" not in operation["responses"]


def test_exact_get_has_no_acting_identity_or_visibility_filter() -> None:
    operation = _operation("get", "/openapi/v1/bots/{bot_id}")

    assert set(_parameters(operation)) == {"bot_id"}
    assert operation["x-avernet-behavior"] == {
        "result_kinds": ["bot", "human"],
        "visibility_filter": "none",
    }
    assert _success_data(operation)["discriminator"]["propertyName"] == "kind"
    assert operation["responses"]["404"]["x-error-codes"] == ["bot_not_found"]


def test_bot_patch_exposes_only_the_approved_mutable_fields() -> None:
    operation = _operation("patch", "/openapi/v1/bots/{bot_id}")
    request = operation["requestBody"]["content"]["application/json"]["schema"]

    assert set(_parameters(operation)) == {"bot_id"}
    assert request["minProperties"] == 1
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == {"name", "visibility", "status", "descriptor"}
    descriptor = request["properties"]["descriptor"]
    assert descriptor["minProperties"] == 1
    assert descriptor["additionalProperties"] is False
    assert set(descriptor["properties"]) == {"summary", "domains", "skills", "scopes"}
    assert operation["x-avernet-behavior"] == {
        "authorization": "created_by_matches_current_staff_no",
        "missing_created_by": "forbidden",
        "descriptor_kind": "bot_only",
        "descriptor_arrays": "replace",
    }
    assert _success_data(operation)["discriminator"]["propertyName"] == "kind"


def test_mine_filters_both_kinds_without_an_all_enum_value() -> None:
    operation = _operation("get", "/openapi/v1/bots/mine")
    parameters = _parameters(operation)

    assert set(parameters) == {"kind", "name", "status", "reachability", "offset", "limit"}
    assert parameters["kind"]["schema"]["enum"] == ["bot", "human"]
    assert "default" not in parameters["kind"]["schema"]
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert operation["x-avernet-behavior"] == {
        "owner_field": "created_by",
        "owner_value": "current_staff_no",
        "omitted_kind": "all",
        "reachability_applies_to": "bot",
        "ordering": ["created_at_desc", "bot_id_asc"],
    }

    data = _success_data(operation)
    assert data["properties"]["items"]["items"]["discriminator"]["propertyName"] == "kind"

