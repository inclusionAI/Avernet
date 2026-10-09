"""Contract tests for ``GET /openapi/v1/bots/collaborations``."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.collaborating_bots import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.principal import require_acting_caller
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_collaborator.models import CollaboratorRecord
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from tests.community.adapters.http.openapi_v1.conftest import user_scoped_client


def _relation(*, bot_id: str = "shared-1", owner_id: str = "owner-1"):
    return CollaboratorRecord(
        id=17,
        bot_pk=91,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id="collab-1",
        user_name="Collaborator",
        role="admin",
        operator_id=owner_id,
        env="pre",
        gmt_create=datetime(2026, 9, 1, 10, 30),
        gmt_modified=datetime(2026, 9, 1, 10, 30),
    )


def _client(*, bots, collaborators, caller=None):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=bots)
            binder.bind(CollaboratorServiceProtocol, to=collaborators)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "collab-1"}
    if caller is not None:
        app.dependency_overrides[require_acting_caller] = lambda: caller
    attach_injector(app, Injector([_M()]))
    return user_scoped_client(app, "collab-1")


def test_lists_only_collaborations_and_keeps_entity_and_owner_separate():
    bots = MagicMock()
    bots.list_bots_by_owner_bot_pairs.return_value = {
        "total": 1,
        "items": [
            {
                "id": 91,
                "bot_id": "shared-1",
                "bot_name": "Shared Bot",
                "bot_desc": "Works together",
                "entity_id": "team-entity-8",
                "owner_id": "owner-1",
                "active_engine": "teclaw",
                "bot_type": "personal",
                "status": "ACTIVE",
                "template_config": {"token": "test-token"},
            }
        ],
    }
    collaborators = MagicMock()
    collaborators.list_user_collaborations.return_value = [_relation()]

    response = _client(bots=bots, collaborators=collaborators).get(
        "/openapi/v1/bots/collaborations", params={"page": 2, "page_size": 7}
    )

    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"] == [
        {
            "bot_id": "shared-1",
            "bot_name": "Shared Bot",
            "bot_desc": "Works together",
            "entity_id": "team-entity-8",
            "owner_id": "owner-1",
            "engine": "teclaw",
            "cluster_name": "ANDC",
            "bot_type": "personal",
            "status": "ACTIVE",
            "collaboration": {
                "id": 17,
                "role": "admin",
                "joined_at": "2026-09-01T10:30:00",
            },
        }
    ]
    assert "owner_entity_id" not in data["items"][0]
    assert "template_config" not in data["items"][0]
    collaborators.list_user_collaborations.assert_called_once_with("collab-1")
    bots.list_bots_by_owner_bot_pairs.assert_called_once_with(
        pairs=[("shared-1", "owner-1")], page=2, page_size=7
    )


def test_empty_collaborations_do_not_query_bots():
    bots = MagicMock()
    collaborators = MagicMock()
    collaborators.list_user_collaborations.return_value = []

    response = _client(bots=bots, collaborators=collaborators).get(
        "/openapi/v1/bots/collaborations"
    )

    assert response.status_code == 200, response.json()
    assert response.json()["data"] == {"total": 0, "items": []}
    bots.list_bots_by_owner_bot_pairs.assert_not_called()


def test_application_filter_uses_exact_bot_and_owner_pair():
    bots = MagicMock()
    bots.list_bots_by_owner_bot_pairs.return_value = {"total": 0, "items": []}
    collaborators = MagicMock()
    collaborators.list_user_collaborations.return_value = [
        _relation(bot_id="default", owner_id="owner-1"),
        _relation(bot_id="default", owner_id="owner-2"),
    ]
    grants = MagicMock()
    grants.list_for_app.return_value = [
        MagicMock(bot_id="default", owner_id="owner-2")
    ]
    caller = ActingCaller(user_id="collab-1", app_id=42, grants=grants)

    response = _client(
        bots=bots, collaborators=collaborators, caller=caller
    ).get("/openapi/v1/bots/collaborations")

    assert response.status_code == 200, response.json()
    bots.list_bots_by_owner_bot_pairs.assert_called_once_with(
        pairs=[("default", "owner-2")], page=1, page_size=20
    )
