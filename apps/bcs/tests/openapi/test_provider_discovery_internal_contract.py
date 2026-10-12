"""Provider discovery freezes the credential-free legacy wire format."""

import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest

BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract, validate_contract

PATH = "/api/v1/collaboration/providers/by-slug/{slug}"


def _contract():
    return load_contract(CONTRACT_ROOT, entrypoint="internal.yaml")


def _schema(operation, status):
    return operation["responses"][status]["content"]["application/json"]["schema"]


def test_discovery_is_internal_credential_free_and_matches_gateway_snapshot():
    contract = _contract()
    operation = contract["paths"][PATH]["get"]
    assert operation["security"] == []
    assert operation["x-avernet-security"] == {}
    assert operation["x-avernet-raw-response"] is True
    assert validate_contract(contract, path_prefix="/api/v1/collaboration/") == []
    snapshot = BCS_ROOT.parent / "gateway/configs/schemas/bcn.internal.openapi.json"
    assert json.loads(snapshot.read_text())["paths"][PATH]["get"] == operation
    assert PATH not in load_contract(CONTRACT_ROOT)["paths"]


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("auth_mode", ["static_bearer", "agentpass", "provider_admin"])
def test_public_projection_supports_all_modes_and_excludes_private_fields(enabled, auth_mode):
    operation = _contract()["paths"][PATH]["get"]
    payload = {
        "provider_id": "prv_local", "slug": "local", "name": "Local",
        "auth_mode": auth_mode, "enabled": enabled, "protocol_version": "2.0",
        "created_at": 1, "updated_at": 2,
    }
    schema = _schema(operation, "200")
    jsonschema.validate(payload, schema)
    for field in ("admin_token", "webhook_url", "auth", "bcs_to_provider_token"):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**payload, field: "private"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"code": 20000, "data": payload}, schema)


@pytest.mark.parametrize("slug", ["Invalid_slug", "-local", "local-", "", "a" * 65])
def test_slug_parameter_rejects_invalid_syntax(slug):
    schema = _contract()["paths"][PATH]["get"]["parameters"][0]["schema"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(slug, schema)


def test_errors_preserve_raw_status_and_validator_exception_is_scoped():
    contract = _contract()
    operation = contract["paths"][PATH]["get"]
    assert set(operation["responses"]) == {"200", "400", "404", "500"}
    for status in ("400", "404", "500"):
        schema = _schema(operation, status)
        jsonschema.validate({"error": "lookup failed", "status": int(status)}, schema)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"error": "lookup failed", "status": 200}, schema)
    other_path = copy.deepcopy(contract)
    other_path["paths"]["/api/v1/collaboration/other"] = other_path["paths"].pop(PATH)
    assert any("not an envelope" in error for error in validate_contract(
        other_path, path_prefix="/api/v1/collaboration/"))
    malformed = copy.deepcopy(contract)
    schema = _schema(malformed["paths"][PATH]["get"], "400")
    schema["properties"]["status"]["const"] = 200
    assert any("invalid legacy Provider error schema" in error for error in validate_contract(
        malformed, path_prefix="/api/v1/collaboration/"))
