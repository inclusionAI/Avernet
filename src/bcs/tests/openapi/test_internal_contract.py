"""Approved BCN internal OpenAPI V1 contract inventory."""

import sys
from pathlib import Path

BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract  # noqa: E402

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

EXPECTED_OPERATIONS = {
    ("get", "/api/v1/collaboration/bots/{bot_id}/candidates"),
    ("get", "/api/v1/collaboration/bots/{bot_id}/candidates/search"),
    ("get", "/api/v1/collaboration/sessions/{session_id}/files"),
    ("post", "/api/v1/collaboration/sessions/{session_id}/files"),
    ("get", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}"),
    ("delete", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}"),
    ("get", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/content"),
    ("put", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/content"),
    ("post", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/complete"),
    ("post", "/api/v1/collaboration/sessions/{session_id}/files/{file_id}/share"),
    ("get", "/api/v1/collaboration/sessions/shared-file/content"),
}


def _actual_operations():
    contract = load_contract(CONTRACT_ROOT, entrypoint="internal.yaml")
    return {
        (method, path)
        for path, path_item in contract["paths"].items()
        for method in path_item
        if method.lower() in HTTP_METHODS
    }


def test_contract_contains_exactly_the_11_approved_internal_operations() -> None:
    assert _actual_operations() == EXPECTED_OPERATIONS


def test_all_operations_share_the_internal_ownership_prefix() -> None:
    assert all(
        path.startswith("/api/v1/collaboration/")
        for _, path in _actual_operations()
    )


def test_contract_excludes_public_openapi_routes() -> None:
    actual = _actual_operations()
    assert not any(path.startswith("/openapi/v1/") for _, path in actual)
