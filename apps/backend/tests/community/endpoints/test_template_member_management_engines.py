"""Run the live member-management story against real in-process HTTP handlers."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.community.acceptance.bot_collaborator import test_member_management_live as story


@pytest.mark.parametrize(
    ("active_engine", "allowed"),
    [("claude_code", True), ("aicoding", True), ("openclaw", False)],
)
def test_persisted_template_member_management_engine_gate(
    app_with_testing_modules, monkeypatch, active_engine, allowed,
):
    # Reuse the exact live story and its SQL seed. Only replace the network
    # transport; real routes, services and SQLite repositories remain wired.
    client = TestClient(app_with_testing_modules)

    def local_client(*, base_url, headers, timeout):
        client.headers.update(headers)
        return client

    monkeypatch.setattr(story, "httpx", SimpleNamespace(Client=local_client))
    story.test_template_ext_member_management_allows_live_collaborator_add(
        "http://testserver", active_engine, allowed,
    )
