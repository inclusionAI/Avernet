"""The pinned catalog exposes only the owner management contract, not credentials."""

import json
from pathlib import Path


def test_dws_management_routes_and_strict_inputs_are_published():
    path = Path(__file__).resolve().parents[2] / "configs/schemas/bots.openapi.json"
    document = json.loads(path.read_text())
    prefix = "/openapi/v1/bots/{bot_id}/dws-hosting"
    expected = {
        "/eligibility": {"get"},
        "": {"get"},
        "/settings": {"get", "put"},
        "/enabled": {"put"},
        "/authorization": {"post"},
        "/authorization/check": {"post"},
        "/conversations": {"get"},
        "/takeover": {"put"},
    }
    for suffix, methods in expected.items():
        assert set(document["paths"][prefix + suffix]) == methods
        for operation in document["paths"][prefix + suffix].values():
            assert {"401", "404", "409", "503"} <= set(operation["responses"])
            user_id = next(p for p in operation["parameters"] if p["name"] == "user_id")
            assert user_id["in"] == "query" and user_id["required"] is True
    for name in ["Settings", "SettingsUpdate", "EnabledUpdate", "TakeoverUpdate"]:
        schema = document["components"]["schemas"][name]
        assert schema["additionalProperties"] is False
        assert (
            not {"owner_id", "token", "command", "gateway_url"}
            & schema["properties"].keys()
        )
