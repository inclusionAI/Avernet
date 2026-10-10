"""Run the live member-management story against real in-process HTTP handlers."""
import pytest
from fastapi.testclient import TestClient

from tests.community.acceptance.bot_collaborator.test_member_management_live import (
    assert_template_member_management,
)


@pytest.mark.parametrize(
    ("active_engine", "allowed"),
    [("claude_code", True), ("aicoding", True), ("openclaw", False)],
)
def test_persisted_template_member_management_engine_gate(
    app_with_testing_modules, active_engine, allowed,
):
    # Reuse SQL seeds and assertions with an explicit client, without patching
    # the transport or replacing any handler, service or repository.
    owner_id = "template_member_owner"
    with TestClient(
        app_with_testing_modules, headers={"x-user-id": owner_id},
    ) as client:
        assert_template_member_management(
            client, owner_id=owner_id, active_engine=active_engine, allowed=allowed,
        )
