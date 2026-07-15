"""Tests for collaborator-protected Harness endpoints."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agentclaw.community.core.harness.repository_protocol import (
    HarnessScanRecordRepository,
)
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot


@pytest.fixture
def client(app_with_testing_modules):
    return TestClient(app_with_testing_modules)


def _discard_background_task(coroutine):
    coroutine.close()


def test_diagnose_rejects_nonexistent_bot_without_creating_scan(client, world):
    make_staff_user(world, user_id="u_owner")
    scan_repo = world.get(HarnessScanRecordRepository)

    with (
        patch.object(scan_repo, "create", wraps=scan_repo.create) as create_scan,
        patch(
            "agentclaw.community.adapters.http.harness.router.asyncio.create_task",
            side_effect=_discard_background_task,
        ) as create_task,
    ):
        response = client.post(
            "/api/harness/diagnose",
            headers={"x-user-id": "u_owner"},
            json={
                "bot_id": "nonexistent_bot",
                "entity_id": "u_owner",
                "entity_type": "staff",
            },
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Bot not found: nonexistent_bot"
    create_scan.assert_not_called()
    create_task.assert_not_called()


def test_diagnose_starts_scan_for_existing_bot(client, world):
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner")

    with patch(
        "agentclaw.community.adapters.http.harness.router.asyncio.create_task",
        side_effect=_discard_background_task,
    ) as create_task:
        response = client.post(
            "/api/harness/diagnose",
            headers={"x-user-id": "u_owner"},
            json={
                "bot_id": "bot_test",
                "entity_id": "u_owner",
                "entity_type": "staff",
            },
        )

    assert response.status_code == 202
    assert response.json()["status"] == "scanning"
    create_task.assert_called_once()
