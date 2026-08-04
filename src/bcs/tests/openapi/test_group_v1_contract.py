import sys
from pathlib import Path

import yaml

BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.bundle_openapi_contract import bundle_contract  # noqa: E402
from scripts.validate_openapi_contract import (  # noqa: E402
    _json_pointer,
    load_contract,
    validate_contract,
)

GROUPS_PATH = "/openapi/v1/collaboration/groups"
GROUP_PATH = "/openapi/v1/collaboration/groups/{group_id}"
BOT_GROUPS_PATH = "/openapi/v1/collaboration/bots/{bot_id}/groups"


def test_contract_obeys_bcn_openapi_rules() -> None:
    contract = load_contract(CONTRACT_ROOT)

    assert validate_contract(contract) == []


def test_group_contract_keeps_the_approved_compatibility_surface() -> None:
    contract = load_contract(CONTRACT_ROOT)
    serialized = repr(contract)

    assert "target_actor_id" in serialized
    assert "target_bot_uuid" not in serialized
    assert "bot_final_delivery" in serialized
    assert "sender_routes" not in serialized
    assert "routing_policy" not in serialized

    list_operation = contract["paths"][BOT_GROUPS_PATH]["get"]
    assert list_operation["operationId"] == "list_groups"
    query_names = {
        parameter["name"]
        for parameter in list_operation["parameters"]
        if parameter["in"] == "query"
    }
    assert query_names == {
        "offset",
        "limit",
        "q",
        "membership",
        "kind",
        "strategy",
    }
    path_names = {
        parameter["name"]
        for parameter in list_operation["parameters"]
        if parameter["in"] == "path"
    }
    assert path_names == {"bot_id"}

    assert (
        contract["paths"][GROUPS_PATH]["post"]["responses"]["201"]["content"][
            "application/json"
        ]["schema"]["properties"]["code"]["const"]
        == 20_100
    )
    assert (
        contract["paths"][GROUP_PATH]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]["properties"]["code"]["const"]
        == 20_000
    )
    assert set(
        contract["paths"][GROUPS_PATH]["post"]["responses"]["404"][
            "x-error-codes"
        ]
    ) == {"bot_not_found", "collaboration_definition_not_found"}
    assert set(
        contract["paths"][GROUPS_PATH]["post"]["responses"]["409"][
            "x-error-codes"
        ]
    ) == {"conflict", "non_public_participant"}
    assert set(
        contract["paths"][GROUPS_PATH]["post"]["responses"]["400"][
            "x-error-codes"
        ]
    ) == {
        "invalid_request",
        "invalid_participant",
        "invalid_participant_binding",
    }
    assert set(
        contract["paths"][GROUP_PATH]["get"]["responses"]["409"][
            "x-error-codes"
        ]
    ) == {"state_machine_definition_missing"}
    assert (
        contract["paths"][GROUP_PATH]["get"]["responses"]["400"][
            "x-error-codes"
        ]
        == ["invalid_request"]
    )
    assert set(
        contract["paths"][GROUP_PATH]["patch"]["responses"][
            "404"
        ]["x-error-codes"]
    ) == {"group_not_found", "bot_not_found"}
    assert set(
        contract["paths"][GROUP_PATH]["patch"]["responses"][
            "409"
        ]["x-error-codes"]
    ) == {
        "conflict",
        "non_public_participant",
        "state_machine_definition_missing",
    }
    assert (
        contract["paths"][GROUP_PATH]["delete"]["responses"][
            "400"
        ]["x-error-codes"]
        == ["invalid_request"]
    )
    assert (
        contract["paths"][GROUPS_PATH]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["properties"]["code"]["const"]
        == 20_000
    )


def test_group_detail_uses_implicit_human_or_owned_bot_participant_access() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][GROUP_PATH]["get"]

    assert "view_bot_id" not in {
        parameter["name"]
        for parameter in operation["parameters"]
        if parameter["in"] == "query"
    }
    forbidden = operation["responses"]["403"]
    assert forbidden["x-error-codes"] == ["forbidden"]
    description = forbidden["description"]
    assert "Human Actor" in description
    assert "created by that Human" in description
    assert "Group Participant" in description


def test_contract_bundles_to_a_deterministic_document(
    tmp_path: Path,
) -> None:
    output = bundle_contract(CONTRACT_ROOT, tmp_path)
    first = output.read_text(encoding="utf-8")
    second = bundle_contract(CONTRACT_ROOT, tmp_path).read_text(encoding="utf-8")

    assert first == second
    assert "$ref:" not in first
    assert "operationId: list_groups" in first


def test_bundled_discriminator_mappings_resolve_inside_the_document(
    tmp_path: Path,
) -> None:
    output = bundle_contract(CONTRACT_ROOT, tmp_path)
    contract = yaml.safe_load(output.read_text(encoding="utf-8"))

    def visit(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        discriminator = value.get("discriminator")
        if isinstance(discriminator, dict):
            for target in discriminator.get("mapping", {}).values():
                assert target.startswith("#/")
                _json_pointer(contract, target[1:])
        for item in value.values():
            visit(item)

    visit(contract)


def test_add_group_participant_accepts_only_actor_id() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/groups/{group_id}/participants"
    ]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert schema == {
        "type": "object",
        "additionalProperties": False,
        "required": ["actor_id"],
        "properties": {"actor_id": {"type": "string"}},
    }


def test_update_group_participant_endpoint_is_not_in_public_contract() -> None:
    contract = load_contract(CONTRACT_ROOT)
    path_item = contract["paths"][
        "/openapi/v1/collaboration/groups/{group_id}/participants/{actor_id}"
    ]

    assert "patch" not in path_item
    assert "delete" in path_item


def test_list_groups_is_scoped_by_path_bot_without_view_bot_query() -> None:
    contract = load_contract(CONTRACT_ROOT)
    path = "/openapi/v1/collaboration/bots/{bot_id}/groups"

    assert path in contract["paths"]
    operation = contract["paths"][path]["get"]
    assert operation["operationId"] == "list_groups"
    path_names = {
        parameter["name"]
        for parameter in operation["parameters"]
        if parameter["in"] == "path"
    }
    query_names = {
        parameter["name"]
        for parameter in operation["parameters"]
        if parameter["in"] == "query"
    }

    assert path_names == {"bot_id"}
    assert query_names == {
        "offset",
        "limit",
        "q",
        "membership",
        "kind",
        "strategy",
    }
    assert "view_bot_id" not in query_names
    assert "get" not in contract["paths"]["/openapi/v1/collaboration/groups"]
    assert "post" in contract["paths"]["/openapi/v1/collaboration/groups"]
