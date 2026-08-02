"""Approved BCN OpenAPI V1 contract inventory.

The contract must expose exactly the approved operations across Bot, Group,
GroupParticipant, Session, SessionParticipant, Invitation, and Friendship /
FriendRequest, and must not expose message-send, Internal API, or routing-only
path aliases.
"""

import sys
from pathlib import Path

BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract  # noqa: E402

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

EXPECTED_OPERATIONS = {
    ("get", "/openapi/v1/bots/{bot_id}/candidates"),
    ("post", "/openapi/v1/bots/query"),
    ("get", "/openapi/v1/bots/{bot_id}"),
    ("patch", "/openapi/v1/bots/{bot_id}"),
    ("get", "/openapi/v1/bots/mine"),
    ("get", "/openapi/v1/bots/collaboration/{bot_uuid}/groups"),
    ("post", "/openapi/v1/groups"),
    ("get", "/openapi/v1/groups/{group_id}"),
    ("patch", "/openapi/v1/groups/{group_id}"),
    ("delete", "/openapi/v1/groups/{group_id}"),
    ("post", "/openapi/v1/groups/{group_id}/participants"),
    ("patch", "/openapi/v1/groups/{group_id}/participants/{actor_id}"),
    ("delete", "/openapi/v1/groups/{group_id}/participants/{actor_id}"),
    ("post", "/openapi/v1/groups/{group_id}/sessions"),
    ("get", "/openapi/v1/groups/{group_id}/sessions"),
    ("get", "/openapi/v1/sessions/{session_id}"),
    ("patch", "/openapi/v1/sessions/{session_id}"),
    ("delete", "/openapi/v1/sessions/{session_id}"),
    ("post", "/openapi/v1/sessions/{session_id}/completion"),
    ("get", "/openapi/v1/sessions/{session_id}/messages"),
    ("post", "/openapi/v1/sessions/{session_id}/participants"),
    ("patch", "/openapi/v1/sessions/{session_id}/participants/{bot_uuid}"),
    ("delete", "/openapi/v1/sessions/{session_id}/participants/{bot_uuid}"),
    ("post", "/openapi/v1/groups/{group_id}/invitations"),
    ("post", "/openapi/v1/sessions/{session_id}/invitations"),
    ("post", "/openapi/v1/invitations/{token}/accept"),
    ("get", "/openapi/v1/bots/collaboration/{bot_uuid}/friendships"),
    ("delete", "/openapi/v1/bots/collaboration/{bot_uuid}/friendships/{friend_bot_uuid}"),
    ("post", "/openapi/v1/bots/collaboration/{bot_uuid}/friend-requests"),
    ("get", "/openapi/v1/bots/collaboration/{bot_uuid}/friend-requests"),
    ("post", "/openapi/v1/friend-requests/{request_id}/accept"),
    ("post", "/openapi/v1/friend-requests/{request_id}/reject"),
}


def _actual_operations():
    contract = load_contract(CONTRACT_ROOT)
    return {
        (method, path)
        for path, path_item in contract["paths"].items()
        for method in path_item
        if method.lower() in HTTP_METHODS
    }


def test_contract_contains_exactly_the_32_approved_operations() -> None:
    assert _actual_operations() == EXPECTED_OPERATIONS


def test_contract_excludes_unapproved_runtime_and_routing_surfaces() -> None:
    actual = _actual_operations()

    assert ("post", "/openapi/v1/sessions/{session_id}/messages") not in actual
    assert ("get", "/openapi/v1/bots") not in actual
    assert ("get", "/openapi/v1/bots/discover") not in actual
    assert ("patch", "/openapi/v1/bots/{bot_id}/descriptor") not in actual
    assert not any(path.startswith("/openapi/v1/bcn/") for _, path in actual)
    assert not any(path.startswith("/openapi/v1/actors/") for _, path in actual)
    assert not any(path.startswith("/openapi/v1/internal/") for _, path in actual)
