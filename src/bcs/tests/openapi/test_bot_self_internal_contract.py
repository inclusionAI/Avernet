"""The Agent identity self lookup is authenticated independently of Bot registration."""

import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest

BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
GATEWAY_SCHEMAS = BCS_ROOT.parent / "gateway" / "configs" / "schemas"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import (
    load_contract,
    validate_contract,
)

PATH = "/api/v1/collaboration/bots/me"


def _contract():
    return load_contract(CONTRACT_ROOT, entrypoint="internal.yaml")


def _operation():
    return _contract()["paths"][PATH]["get"]


def _envelope(data):
    return {"code": 20000, "message": "OK", "data": data, "request_id": "req-001"}


def _registered():
    return {
        "registration_status": "registered",
        "identity": {"agent_code": "agent-001"},
        "bot": {
            "bot_id": "bot-001",
            "name": "Poolab Assistant",
            "summary": "Development assistant",
            "provider_id": "provider-poolab",
            "provider_bot_ref": "agent-001",
        },
    }


def test_bot_self_is_internal_and_authenticates_bearer_in_bcs():
    contract = _contract()
    operation = contract["paths"][PATH]["get"]
    assert set(contract["paths"][PATH]) == {"get"}
    assert PATH not in load_contract(CONTRACT_ROOT)["paths"]
    assert PATH.replace("/api/", "/openapi/") not in load_contract(CONTRACT_ROOT)["paths"]
    assert operation["x-avernet-security"] == {}
    assert operation["security"] == [{"AgentIdentityBearer": []}]
    assert operation.get("parameters", []) == []
    assert contract["components"]["securitySchemes"]["AgentIdentityBearer"]["scheme"] == "bearer"
    assert validate_contract(contract, path_prefix="/api/v1/collaboration/") == []


def test_bot_self_schema_models_registered_unregistered_and_legacy_bots():
    schema = _operation()["responses"]["200"]["content"]["application/json"]["schema"]
    registered = _registered()
    jsonschema.validate(_envelope(registered), schema)
    legacy = copy.deepcopy(registered)
    for key in ("name", "summary", "provider_id", "provider_bot_ref"):
        legacy["bot"][key] = None
    jsonschema.validate(_envelope(legacy), schema)
    jsonschema.validate(
        _envelope({
            "registration_status": "unregistered",
            "identity": {"agent_code": "agent-001"},
            "bot": None,
        }),
        schema,
    )
    invalid_values = [
        {**registered, "registration_status": "unregistered"},
        {**registered, "bot": None},
        {**registered, "identity": {"agent_code": "agent-001", "token": "secret"}},
        {**registered, "bot": {**registered["bot"], "agent_token": "secret"}},
        {**registered, "bot": {**registered["bot"], "provider_id": None}},
    ]
    for invalid in invalid_values:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(_envelope(invalid), schema)


def test_bot_self_errors_and_all_responses_disable_caching():
    responses = _operation()["responses"]
    assert set(responses) == {"200", "401", "403", "409", "500", "502"}
    for response in responses.values():
        assert response["headers"]["Cache-Control"]["schema"]["const"] == "no-store"
    assert responses["401"]["x-error-codes"] == ["unauthenticated"]
    assert responses["403"]["x-error-codes"] == ["forbidden"]
    assert responses["409"]["x-error-codes"] == ["agent_registration_conflict"]
    assert responses["500"]["x-error-codes"] == ["internal_error"]
    assert responses["502"]["x-error-codes"] == ["agent_identity_unavailable"]


def test_gateway_publishes_bot_self_only_in_its_internal_catalog():
    internal = json.loads((GATEWAY_SCHEMAS / "bcn.internal.openapi.json").read_text())
    public = json.loads((GATEWAY_SCHEMAS / "bcn.openapi.json").read_text())
    assert internal["paths"][PATH]["get"] == _operation()
    assert PATH not in public["paths"]
    assert PATH.replace("/api/", "/openapi/") not in public["paths"]
