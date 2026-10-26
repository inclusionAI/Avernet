"""The shipped Gateway publishes expert creation and its read-only source name."""

import json
from pathlib import Path

_GATEWAY = Path(__file__).resolve().parents[4]


def test_expert_creation_uses_original_bot_create_and_accepted_contract():
    spec = json.loads((_GATEWAY / "configs/schemas/bots.openapi.json").read_text())
    operation = spec["paths"]["/openapi/v1/experts/{expert_id}/bots"]["post"]
    assert (
        operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/BotCreate"
    )
    assert (
        operation["responses"]["202"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/Envelope_BotCreateWithManifestAccepted_"
    )
    assert {"expert_id", "user_id"} <= {
        parameter["name"] for parameter in operation["parameters"]
    }
    assert "expert_origin" not in spec["components"]["schemas"]["Bot"]["properties"]
    bot = spec["components"]["schemas"]["Bot"]
    assert bot["properties"]["expert_name"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert "expert_name" not in bot["required"]


def test_inventory_publishes_only_optional_read_only_expert_name():
    spec = json.loads((_GATEWAY / "configs/schemas/bots.openapi.json").read_text())
    schema = spec["components"]["schemas"]["BotInventoryItem"]
    assert schema["properties"]["expert_name"]["readOnly"] is True
    assert "expert_name" not in schema["required"]
    assert "expert_origin" not in schema["properties"]
    operation = spec["paths"]["/openapi/v1/bots/all"]["get"]
    assert "BotInventoryItem" in str(operation["responses"]["200"])
