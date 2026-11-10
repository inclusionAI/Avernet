"""Bot control-plane OpenAPI V1 contract decisions."""

import sys
from pathlib import Path

BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract  # noqa: E402


BOT_OPERATIONS = {
    ("post", "/openapi/v1/collaboration/bots/query"),
    ("get", "/openapi/v1/collaboration/bots/{bot_id}"),
    ("patch", "/openapi/v1/collaboration/bots/{bot_id}"),
    ("get", "/openapi/v1/collaboration/bots/mine"),
}


def _contract():
    return load_contract(CONTRACT_ROOT)


def _operation(method: str, path: str):
    return _contract()["paths"][path][method]


def _public_contract():
    return load_contract(CONTRACT_ROOT)


def _internal_contract():
    return load_contract(CONTRACT_ROOT, entrypoint="internal.yaml")


def _public_operation(method: str, path: str):
    return _public_contract()["paths"][path][method]


def _internal_operation(method: str, path: str):
    return _internal_contract()["paths"][path][method]


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
        assert operation["x-avernet-security"] == {
            "user": "required",
            "app": "required",
        }


def test_bot_management_operations_share_the_collaboration_prefix() -> None:
    assert all(
        path.startswith("/openapi/v1/collaboration/bots/")
        for _, path in BOT_OPERATIONS
    )


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
        "avatar_url",
        "kind",
        "name",
        "visibility",
        "status",
        "env",
        "user_visibility",
        "friend_ext",
        "friend_check_in_strategy",
        "created_at",
        "updated_at",
    }
    physical = variants["bot"]
    human = variants["human"]

    for variant in (physical, human):
        avatar = variant["properties"]["avatar_url"]
        assert avatar["type"] == "string"
        assert avatar["nullable"] is True

    assert physical["additionalProperties"] is False
    assert human["additionalProperties"] is False
    assert set(physical["required"]) == common_required | {
        "descriptor",
        "reachability",
        "task_claim_mode",
        "task_dream_mode",
    }
    assert set(human["required"]) == common_required
    assert "created_by" in physical["properties"]
    assert "created_by" in human["properties"]
    assert "created_by" not in physical["required"]
    assert "created_by" not in human["required"]

    physical_only = {
        "descriptor",
        "reachability",
        "provider",
        "agent_code",
        "task_claim_mode",
        "task_dream_mode",
    }
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
    operation = _public_operation(
        "get", "/openapi/v1/collaboration/bots/{bot_id}/candidates"
    )
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
    assert "including Human Actor" in operation["summary"]

    assert operation["x-avernet-behavior"] == {
        "acting_bot": ["managed_physical_bot", "current_human_actor"],
        "result_kind": "bot",
        "environment": "same_as_acting_bot",
        "exclude_self": True,
        "viewer_visibility": {"human": "user_visibility", "bot": "visibility"},
        "friend_source": "approved_connect_edge",
        "purpose_visibility": {
            "discovery": ["public", "protected"],
            "collaboration": ["public", "friend"],
        },
        "status_filter": "none",
        "reachability_filter": "none",
        "ordering": ["created_at_desc", "bot_id_asc"],
    }
    assert operation["responses"]["400"]["x-error-codes"] == ["invalid_request"]
    assert operation["responses"]["403"]["x-error-codes"] == ["forbidden"]

    candidate = _success_data(operation)["properties"]["items"]["items"]
    assert set(candidate["required"]) == {"bot", "is_friend"}
    assert candidate["properties"]["bot"]["properties"]["kind"]["const"] == "bot"


def test_candidates_publish_viewer_visibility_and_edge_friend_source() -> None:
    candidates = _public_operation(
        "get", "/openapi/v1/collaboration/bots/{bot_id}/candidates"
    )
    alias = _public_operation(
        "get", "/openapi/v1/collaboration/bots/{bot_id}/eligible-candidates"
    )
    description = candidates["description"]

    assert candidates["x-avernet-behavior"]["friend_source"] == "approved_connect_edge"
    assert candidates["x-avernet-behavior"]["viewer_visibility"] == {
        "human": "user_visibility",
        "bot": "visibility",
    }
    assert "applies before pagination" in description
    assert "approved Connect edges" in description
    assert "only recommended candidate entry point" in description
    assert candidates["x-avernet-security"] == {
        "user": "required",
        "app": "required",
    }
    assert "deprecated" not in candidates
    assert alias["deprecated"] is True


def test_eligible_candidates_is_a_deprecated_compatibility_alias() -> None:
    candidates = _public_operation(
        "get", "/openapi/v1/collaboration/bots/{bot_id}/candidates"
    )
    alias = _public_operation(
        "get", "/openapi/v1/collaboration/bots/{bot_id}/eligible-candidates"
    )
    candidate_parameters = _parameters(candidates)
    alias_parameters = _parameters(alias)

    assert alias["deprecated"] is True
    assert alias["operationId"] == "list_eligible_bot_candidates"
    assert "delegates" in alias["description"]
    assert "/openapi/v1/collaboration/bots/{bot_id}/candidates" in alias["description"]
    assert set(alias_parameters) == set(candidate_parameters)
    for name, parameter in candidate_parameters.items():
        assert alias_parameters[name]["schema"] == parameter["schema"], name
    assert (
        alias["responses"]["200"]["content"]
        == candidates["responses"]["200"]["content"]
    )
    assert alias["x-avernet-behavior"] == candidates["x-avernet-behavior"]


def test_candidate_search_allows_authenticated_read_only_human_browsers() -> None:
    operation = _internal_operation(
        "get", "/api/v1/collaboration/bots/{bot_id}/candidates/search"
    )
    description = operation["description"]

    assert "read-only consumer" in description
    assert "authenticated Human browser" in description
    assert "no /openapi/v1 alias" in description
    assert "at most one provider recall of up to 20" in description
    assert "authorized subset" in description
    assert "provider order" in description
    assert "no top-up query" in description
    assert "falls back" in description
    assert operation["x-avernet-security"] == {
        "user": "optional",
        "app": "optional",
        "bot": "optional",
    }
    behavior = operation["x-avernet-behavior"]
    assert behavior["friend_source"] == "approved_connect_edge"
    assert behavior["viewer_visibility"] == {
        "human": "user_visibility",
        "bot": "visibility",
    }
    assert behavior["primary_ordering"] == "provider_order"
    data = _success_data(operation)
    assert data["properties"]["search_mode"]["enum"] == [
        "empty_query",
        "semantic",
        "name_fallback",
    ]


def test_eligible_candidates_contract_uses_the_new_route_name() -> None:
    operation = _public_operation(
        "get", "/openapi/v1/collaboration/bots/{bot_id}/eligible-candidates"
    )
    parameters = _parameters(operation)

    assert operation["operationId"] == "list_eligible_bot_candidates"
    assert set(parameters) == {"bot_id", "purpose", "name", "offset", "limit"}
    assert operation["x-avernet-security"] == {
        "user": "required",
        "app": "required",
    }
    assert operation["summary"].startswith("Deprecated compatibility alias of")
    assert "candidate list" in operation["summary"]
    assert operation["responses"]["403"]["x-error-codes"] == ["forbidden"]
    candidate = _success_data(operation)["properties"]["items"]["items"]
    assert set(candidate["required"]) == {"bot", "is_friend"}


def test_candidate_search_contract_matches_legacy_search_semantics() -> None:
    operation = _internal_operation(
        "get", "/api/v1/collaboration/bots/{bot_id}/candidates/search"
    )
    parameters = _parameters(operation)

    assert operation["operationId"] == "search_bot_candidates"
    assert operation["x-avernet-security"] == {
        "user": "optional",
        "app": "optional",
        "bot": "optional",
    }
    assert operation["responses"]["401"]["description"] == (
        "A supplied Gateway Principal is invalid."
    )
    assert operation["responses"]["403"]["description"] == (
        "BCS requires a usable Human identity; the current Human must manage "
        "the selected physical Bot, or the selected Human Actor must represent "
        "that Human."
    )
    assert set(parameters) == {"bot_id", "q", "purpose"}
    assert parameters["q"].get("required", False) is False
    assert parameters["q"]["schema"] == {"type": "string"}
    assert parameters["purpose"]["schema"] == {
        "type": "string",
        "enum": ["discovery", "collaboration"],
        "default": "discovery",
    }
    assert {
        "ctoken",
        "current_bot_uuid",
        "cooperatable_only",
        "name",
        "offset",
        "limit",
    }.isdisjoint(parameters)

    assert operation["x-avernet-behavior"] == {
        "legacy_equivalent": "/actors/search",
        "acting_bot": ["managed_physical_bot", "current_human_actor"],
        "result_kind": "bot",
        "purpose_mapping": {
            "discovery": {"cooperatable_only": False},
            "collaboration": {"cooperatable_only": True},
        },
        "top_k": 20,
        "primary_ordering": "provider_order",
        "friend_source": "approved_connect_edge",
        "viewer_visibility": {
            "human": "user_visibility",
            "bot": "visibility",
        },
        "fallback": "trimmed_case_insensitive_name_substring",
        "empty_query": {
            "equivalent_inputs": ["omitted", "empty", "whitespace"],
            "downstream_search": "skip",
        },
        "search_modes": ["empty_query", "semantic", "name_fallback"],
    }

    data = _success_data(operation)
    assert data["additionalProperties"] is False
    assert set(data["required"]) == {"items", "search_mode"}
    assert set(data["properties"]) == {"items", "search_mode"}
    item = data["properties"]["items"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"bot", "is_friend", "tags"}
    assert set(item["properties"]) == {
        "bot",
        "is_friend",
        "tags",
        "score",
        "short_profile",
    }
    assert item["properties"]["bot"]["properties"]["kind"]["const"] == "bot"
    assert item["properties"]["tags"]["additionalProperties"] is True
    assert item["properties"]["score"] == {
        "type": "number",
        "format": "double",
        "description": "Semantic relevance score. Omitted for empty-query and name-fallback results.",
    }
    assert item["properties"]["short_profile"] == {
        "type": "string",
        "description": "Short semantic recommendation profile. Omitted for empty-query and name-fallback results.",
    }
    assert data["properties"]["search_mode"]["enum"] == [
        "empty_query",
        "semantic",
        "name_fallback",
    ]
    serialized = repr(data)
    assert "context" not in serialized
    assert "recommend_response" not in serialized


def test_batch_query_is_sparse_ordered_and_not_visibility_filtered() -> None:
    operation = _operation("post", "/openapi/v1/collaboration/bots/query")
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
    operation = _operation("get", "/openapi/v1/collaboration/bots/{bot_id}")

    assert set(_parameters(operation)) == {"bot_id"}
    assert operation["x-avernet-behavior"] == {
        "result_kinds": ["bot", "human"],
        "visibility_filter": "none",
    }
    assert _success_data(operation)["discriminator"]["propertyName"] == "kind"
    assert operation["responses"]["404"]["x-error-codes"] == ["bot_not_found"]


def test_bot_patch_exposes_only_the_approved_mutable_fields() -> None:
    operation = _operation("patch", "/openapi/v1/collaboration/bots/{bot_id}")
    request = operation["requestBody"]["content"]["application/json"]["schema"]

    assert set(_parameters(operation)) == {"bot_id"}
    assert request["minProperties"] == 1
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == {
        "name",
        "visibility",
        "status",
        "descriptor",
        "task_claim_mode",
        "task_dream_mode",
        "user_visibility",
        "friend_ext",
        "friend_check_in_strategy",
    }
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
    operation = _operation("get", "/openapi/v1/collaboration/bots/mine")
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
        "ordering": ["human_self_first", "reachable_first", "created_at_desc", "bot_id_asc"],
    }

    data = _success_data(operation)
    assert data["properties"]["items"]["items"]["discriminator"]["propertyName"] == "kind"
