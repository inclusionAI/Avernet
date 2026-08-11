"""Contract tests for the BCN OpenAPI V1 Session File surface."""

import sys
from pathlib import Path


BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract, validate_contract  # noqa: E402


PROTECTED_OPERATIONS = {
    ("get", "/openapi/v1/collaboration/sessions/{session_id}/files"),
    ("post", "/openapi/v1/collaboration/sessions/{session_id}/files"),
    ("get", "/openapi/v1/collaboration/sessions/{session_id}/files/{file_id}"),
    ("delete", "/openapi/v1/collaboration/sessions/{session_id}/files/{file_id}"),
    ("get", "/openapi/v1/collaboration/sessions/{session_id}/files/{file_id}/content"),
    ("put", "/openapi/v1/collaboration/sessions/{session_id}/files/{file_id}/content"),
    ("post", "/openapi/v1/collaboration/sessions/{session_id}/files/{file_id}/complete"),
    ("post", "/openapi/v1/collaboration/sessions/{session_id}/files/{file_id}/share"),
}
SHARED_CONTENT = "/openapi/v1/collaboration/sessions/shared-file/content"


def _contract():
    return load_contract(CONTRACT_ROOT)


def test_session_file_operations_and_identity_boundaries_are_declared() -> None:
    contract = _contract()
    for method, path in PROTECTED_OPERATIONS:
        operation = contract["paths"][path][method]
        assert operation["x-avernet-security"] == {
            "user": "optional",
            "app": "optional",
            "bot": "optional",
        }
        assert operation["x-bcn-identity-policy"] == "human_or_owned_bot"

    assert contract["paths"][SHARED_CONTENT]["get"]["x-avernet-security"] == {}
    assert "x-bcn-identity-policy" not in contract["paths"][SHARED_CONTENT]["get"]


def test_session_file_contract_excludes_deferred_or_unapproved_routes() -> None:
    paths = _contract()["paths"]
    assert "/openapi/v1/collaboration/sessions/{session_id}/files/capabilities" not in paths
    assert "/openapi/v1/collaboration/sessions/shared-file/meta" not in paths


def test_content_queries_match_the_v1_adapter_contract() -> None:
    contract = _contract()
    protected = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}/files/{file_id}/content"
    ]["get"]
    assert {parameter["name"] for parameter in protected["parameters"]} == {
        "session_id",
        "file_id",
        "show",
    }

    shared = contract["paths"][SHARED_CONTENT]["get"]
    parameters = {parameter["name"]: parameter for parameter in shared["parameters"]}
    assert set(parameters) == {"token", "show"}
    assert parameters["token"]["required"] is True


def test_raw_content_successes_are_explicit_and_contract_is_valid() -> None:
    contract = _contract()
    for path in [
        "/openapi/v1/collaboration/sessions/{session_id}/files/{file_id}/content",
        SHARED_CONTENT,
    ]:
        operation = contract["paths"][path]["get"]
        assert operation["x-avernet-raw-response"] is True
        assert "application/octet-stream" in operation["responses"]["200"]["content"]

    assert validate_contract(contract) == []
