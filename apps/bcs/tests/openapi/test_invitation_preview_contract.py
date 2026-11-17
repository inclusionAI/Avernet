"""Read-only invitation preview is a minimal authenticated membership projection."""
import sys
from pathlib import Path

BCS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BCS_ROOT))
from scripts.validate_openapi_contract import load_contract  # noqa: E402


def test_invitation_preview_is_authenticated_read_only_and_non_cacheable():
    contract = load_contract(BCS_ROOT / "api-contracts" / "v1")
    operation = contract["paths"]["/openapi/v1/collaboration/invitations/{token}"]["get"]
    assert operation["operationId"] == "preview_invitation"
    assert operation["x-avernet-security"] == {"user": "required", "app": "required"}
    assert "requestBody" not in operation
    assert operation["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"] == "private, no-store"
    assert set(operation["responses"]) == {"200", "400", "401", "403", "404", "410", "500"}
    schema = contract["components"]["schemas"]["InvitationPreview"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"target_type", "target_id", "group_id", "already_joined"}
    assert set(schema["properties"]) == set(schema["required"])
