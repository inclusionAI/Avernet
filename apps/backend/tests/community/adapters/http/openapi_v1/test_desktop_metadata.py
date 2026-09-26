"""HTTP contract tests with Mock services; no live desktop claims."""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.desktop_metadata import router
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.link_workflow_service import LinkWorkflowProtocol
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.resources.service import DuplicateResourceError
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)


@pytest.fixture
def world():
    bots, links = Mock(), Mock()
    bots.get_bot.return_value = {"bot_id": "b", "ext": {"avatar_url": "image"}}
    links.list.return_value = []
    links.create = AsyncMock(return_value=[])
    links.update = AsyncMock()
    links.delete = AsyncMock()

    class Bindings(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=bots)
            binder.bind(LinkWorkflowProtocol, to=links)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    attach_injector(app, Injector([Bindings()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, "u1"), bots, links


def test_avatar_uses_legacy_ext_storage_and_owner(world):
    client, bots, _ = world
    response = client.get("/openapi/v1/bots/b/avatar")
    assert response.status_code == 200
    assert response.json()["data"] == {"avatar_url": "image"}
    bots.get_bot.assert_called_with("b", "u1")


def test_avatar_update_uses_shared_service(world):
    client, bots, _ = world
    response = client.put("/openapi/v1/bots/b/avatar", json={"avatar_url": "new-image"})
    assert response.status_code == 200
    assert bots.update_bot.call_args.args == ("b", "u1")
    assert bots.update_bot.call_args.kwargs["ext"] == {"avatar_url": "new-image"}


def test_denied_bot_never_reaches_link_mutation(world):
    client, bots, links = world
    bots.get_bot.side_effect = BotNotFoundError("Bot not found")
    response = client.post(
        "/openapi/v1/bots/b/links",
        json={"links": [{"url": "https://example.com", "link_type": "antcode"}]},
    )
    assert response.status_code == 404
    links.create.assert_not_called()


def test_explicit_null_patch_is_rejected(world):
    client, _, links = world
    response = client.put("/openapi/v1/bots/b/links/r", json={"url": None})
    assert response.status_code == 422
    links.update.assert_not_called()


def test_duplicate_error_is_conflict_not_success(world):
    client, _, links = world
    links.create.side_effect = DuplicateResourceError("Link URL already exists")
    response = client.post(
        "/openapi/v1/bots/b/links",
        json={"links": [{"url": "https://example.com", "link_type": "antcode"}]},
    )
    assert response.status_code == 409
    assert response.json()["code"] != 200000
