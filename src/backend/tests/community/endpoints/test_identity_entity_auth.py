"""Authorization regression tests for entity-level identity reads."""
from __future__ import annotations

from tests.community.framework import CaseInput, ExpectError, endpoint_test


@endpoint_test(
    method="GET",
    path="/api/identity/{entity_type}/{entity_id}/{file_type}",
    scenario="spoofed_operator_forbidden",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": "target-user",
            "file_type": "RULES.md",
        },
        query_params={"user_id": "spoofed-user"},
        headers={"x-user-id": "authenticated-user"},
    ),
    expect=ExpectError(status=403),
)
def entity_identity_read_rejects_spoofed_operator():
    """A query parameter cannot replace the authenticated device identity."""
