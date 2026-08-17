"""Contract tests for the BCN OpenAPI V1 Session File surface."""

import sys
from pathlib import Path


BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract, validate_contract  # noqa: E402


PROTECTED_OPERATIONS = {
    ("get", "/api/v1/collaboration/sessions/{session_id}/files"),
    ("post", "/api/v1/collaboration/sessions/{session_id}/files"),
    ("get", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}"),
    ("delete", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}"),
    ("get", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/content"),
    ("put", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/content"),
    ("post", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/complete"),
    ("post", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/share"),
}
SHARED_CONTENT = "/api/v1/collaboration/sessions/shared-file/content"


def _contract():
    return load_contract(CONTRACT_ROOT, entrypoint="internal.yaml")


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
    assert "/api/v1/collaboration/sessions/{session_id}/files/capabilities" not in paths
    assert "/api/v1/collaboration/sessions/shared-file/meta" not in paths


def test_content_queries_match_the_v1_adapter_contract() -> None:
    contract = _contract()
    protected = contract["paths"][
        "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/content"
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
        "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/content",
        SHARED_CONTENT,
    ]:
        operation = contract["paths"][path]["get"]
        assert operation["x-avernet-raw-response"] is True
        assert "application/octet-stream" in operation["responses"]["200"]["content"]

    assert validate_contract(
        contract,
        path_prefix="/api/v1/collaboration/",
    ) == []


def test_session_file_error_codes_match_the_application_vocabulary() -> None:
    contract = _contract()
    list_responses = contract["paths"][
        "/api/v1/collaboration/sessions/{session_id}/files"
    ]["get"]["responses"]
    assert list_responses["404"]["x-error-codes"] == [
        "session_not_found",
        "group_not_found",
        "session_file_not_found",
    ]

    prepare_responses = contract["paths"][
        "/api/v1/collaboration/sessions/{session_id}/files"
    ]["post"]["responses"]
    assert prepare_responses["413"]["x-error-codes"] == ["file_too_large"]
    assert prepare_responses["502"]["x-error-codes"] == [
        "storage_backend_unavailable"
    ]

    content_responses = contract["paths"][
        "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/content"
    ]["get"]["responses"]
    assert content_responses["422"]["x-error-codes"] == ["file_upload_incomplete"]

    share_responses = contract["paths"][
        "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/share"
    ]["post"]["responses"]
    assert share_responses["422"]["x-error-codes"] == ["file_upload_incomplete"]

    upload_responses = contract["paths"][
        "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/content"
    ]["put"]["responses"]
    assert upload_responses["409"]["x-error-codes"] == ["file_upload_not_pending"]

    delete_responses = contract["paths"][
        "/api/v1/collaboration/sessions/{session_id}/files/{file_id}"
    ]["delete"]["responses"]
    assert delete_responses["502"]["x-error-codes"] == [
        "storage_backend_unavailable"
    ]
