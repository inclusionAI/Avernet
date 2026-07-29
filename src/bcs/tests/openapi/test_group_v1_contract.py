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

EXPECTED_OPERATIONS = {
    ("get", "/openapi/v1/bots/collaboration/{bot_uuid}/groups"),
    ("post", "/openapi/v1/groups"),
    ("get", "/openapi/v1/groups/{group_id}"),
    ("patch", "/openapi/v1/groups/{group_id}"),
    ("delete", "/openapi/v1/groups/{group_id}"),
}


def test_first_batch_contains_exactly_the_five_group_operations() -> None:
    contract = load_contract(CONTRACT_ROOT)

    actual = {
        (method, path)
        for path, path_item in contract["paths"].items()
        for method in path_item
        if method.lower()
        in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
    }

    assert actual == EXPECTED_OPERATIONS
    assert not any(path.startswith("/openapi/v1/bcn/") for _, path in actual)
    assert not any(path.startswith("/openapi/v1/actors/") for _, path in actual)


def test_first_batch_contract_obeys_bcn_openapi_rules() -> None:
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

    list_operation = contract["paths"][
        "/openapi/v1/bots/collaboration/{bot_uuid}/groups"
    ]["get"]
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

    assert (
        contract["paths"]["/openapi/v1/groups"]["post"]["responses"]["201"]["content"][
            "application/json"
        ]["schema"]["properties"]["code"]["const"]
        == 20_100
    )
    assert (
        contract["paths"]["/openapi/v1/groups/{group_id}"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]["properties"]["code"]["const"]
        == 20_000
    )
    assert set(
        contract["paths"]["/openapi/v1/groups"]["post"]["responses"]["404"][
            "x-error-codes"
        ]
    ) == {"bot_not_found", "collaboration_definition_not_found"}
    assert set(
        contract["paths"]["/openapi/v1/groups"]["post"]["responses"]["400"][
            "x-error-codes"
        ]
    ) == {
        "invalid_request",
        "invalid_participant",
        "invalid_participant_binding",
    }
    assert (
        contract["paths"]["/openapi/v1/groups"]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["properties"]["code"]["const"]
        == 20_000
    )


def test_contract_bundles_to_a_deterministic_document(
    tmp_path: Path,
) -> None:
    output = bundle_contract(CONTRACT_ROOT, tmp_path)
    first = output.read_text(encoding="utf-8")
    second = bundle_contract(CONTRACT_ROOT, tmp_path).read_text(encoding="utf-8")

    assert first == second
    assert "$ref:" not in first
    assert "operationId: list_bot_groups" in first


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
