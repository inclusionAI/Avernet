"""Channel Binding OpenAPI V1 contract decisions."""

import sys
from pathlib import Path

BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract  # noqa: E402


BINDINGS_PATH = "/openapi/v1/collaboration/channels/bindings"
BINDING_PATH = "/openapi/v1/collaboration/channels/bindings/{id}"


def _contract():
    return load_contract(CONTRACT_ROOT)


def _config_schema(schema):
    return schema["properties"]["config"]


def _group_context_delivery(config_schema):
    return config_schema["properties"]["group_context_delivery"]


def test_group_context_delivery_is_consistent_across_binding_schemas() -> None:
    contract = _contract()
    create = contract["paths"][BINDINGS_PATH]["post"]["requestBody"]["content"]
    update = contract["paths"][BINDING_PATH]["patch"]["requestBody"]["content"]
    created = contract["paths"][BINDINGS_PATH]["post"]["responses"]["201"]["content"]

    config_schemas = [
        _config_schema(create["application/json"]["schema"]),
        _config_schema(update["application/json"]["schema"]),
        _config_schema(created["application/json"]["schema"]["properties"]["data"]),
    ]
    expected = {"type": "string", "enum": ["send", "inject"], "default": "send"}

    for config_schema in config_schemas:
        option = _group_context_delivery(config_schema)
        assert {key: option[key] for key in expected} == expected
