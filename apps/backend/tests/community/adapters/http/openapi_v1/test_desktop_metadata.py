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


@pytest.mark.parametrize("method", ["put", "delete"])
@pytest.mark.parametrize(
    "resource_id", ["not-a-number", str(1 << 64), str(-(1 << 63) - 1), "9" * 4301]
)
def test_invalid_link_id_is_rejected_without_mutation(world, method, resource_id):
    client, _, links = world
    kwargs = {"json": {}} if method == "put" else {}
    response = getattr(client, method)(
        f"/openapi/v1/bots/b/links/{resource_id}", **kwargs
    )
    assert response.status_code == 422
    assert resource_id not in response.text
    links.update.assert_not_called()
    links.delete.assert_not_called()


@pytest.mark.parametrize("method", ["put", "delete"])
def test_invisible_bot_is_checked_before_link_id(world, method):
    client, bots, links = world
    bots.get_bot.side_effect = BotNotFoundError("Bot not found")
    kwargs = {"json": {}} if method == "put" else {}
    response = getattr(client, method)("/openapi/v1/bots/b/links/invalid", **kwargs)
    assert response.status_code == 404
    links.update.assert_not_called()
    links.delete.assert_not_called()


@pytest.mark.parametrize("method", ["put", "delete"])
def test_missing_principal_precedes_invalid_id(world, method):
    client, bots, links = world
    client.app.dependency_overrides.pop(require_principal)
    kwargs = {"json": {}} if method == "put" else {}
    response = getattr(client, method)("/openapi/v1/bots/b/links/invalid", **kwargs)
    assert response.status_code == 401
    bots.get_bot.assert_not_called()
    links.update.assert_not_called()
    links.delete.assert_not_called()


@pytest.mark.parametrize("method", ["put", "delete"])
def test_grant_refusal_precedes_invalid_id(world, method):
    from agentclaw.community.adapters.http.openapi_v1.errors import (
        GrantNotResolvableError,
    )
    from agentclaw.community.adapters.http.openapi_v1.principal import (
        require_granted_own_bot,
    )

    client, bots, links = world

    def reject_grant():
        raise GrantNotResolvableError()

    client.app.dependency_overrides[require_granted_own_bot] = reject_grant
    kwargs = {"json": {}} if method == "put" else {}
    response = getattr(client, method)("/openapi/v1/bots/b/links/invalid", **kwargs)
    assert response.status_code == 404
    bots.get_bot.assert_not_called()
    links.update.assert_not_called()
    links.delete.assert_not_called()


@pytest.mark.parametrize(
    "encoded_id, expected",
    [
        ("001", "001"),
        ("%2B1", "+1"),
        ("1_0", "1_0"),
        ("%20%31%20", " 1 "),
        ("%D9%A1", "١"),
    ],
)
def test_existing_integer_spellings_reach_workflow(world, encoded_id, expected):
    client, _, links = world
    response = client.delete(f"/openapi/v1/bots/b/links/{encoded_id}")
    assert response.status_code == 200
    links.delete.assert_awaited_once_with("b", "u1", expected)


def test_duplicate_error_is_conflict_not_success(world):
    client, _, links = world
    links.create.side_effect = DuplicateResourceError("Link URL already exists")
    response = client.post(
        "/openapi/v1/bots/b/links",
        json={"links": [{"url": "https://example.com", "link_type": "antcode"}]},
    )
    assert response.status_code == 409
    assert response.json()["code"] != 200000


def test_avatar_rejection_returns_400_without_payload_in_logs(world, monkeypatch):
    from agentclaw.community.adapters.http import error_logging
    from agentclaw.community.core.bot_management.avatar import AvatarTooLargeError

    client, bots, _ = world
    bots.update_bot.side_effect = AvatarTooLargeError(
        "Avatar does not fit in Bot metadata"
    )
    log = Mock()
    monkeypatch.setattr(error_logging.logger, "warning", log)
    value = "data:image/jpeg;base64,unique-private-prefix-" + "x" * 70000
    response = client.put("/openapi/v1/bots/b/avatar", json={"avatar_url": value})
    assert response.status_code == 400
    assert "unique-private-prefix" not in response.text
    assert "unique-private-prefix" not in str(log.call_args)
    assert "data:image/jpeg" not in str(log.call_args)


@pytest.fixture
def real_link_world():
    """Exercise HTTP through the workflow, service and SQLite repository."""
    from contextlib import contextmanager
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from agentclaw.community.core.repository.implementations.platform.resource import (
        ResourceRepository,
    )
    from agentclaw.community.core.resources.link_workflow import LinkWorkflow
    from agentclaw.community.core.resources.service import ResourceService
    from agentclaw.community.plugin_api.models import ResourceModel

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    ResourceModel.__table__.create(engine)

    class DB:
        @contextmanager
        def orm_session(self):
            with Session(engine) as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

    with Session(engine) as session:
        for resource_id, bot, owner, kind in [
            (1, "b", "u1", "link"),
            (2, "other", "u1", "link"),
            (3, "b", "other", "link"),
            (4, "b", "u1", "file"),
            (-1, "b", "u1", "link"),
        ]:
            session.add(
                ResourceModel(
                    id=resource_id,
                    name="original",
                    resource_type=kind,
                    bolt_id=bot,
                    user_id=owner,
                    attributes='{"url":"https://example.com","link_type":"antcode"}',
                )
            )
        session.commit()
    repo = ResourceRepository(DB())
    factory = Mock()
    factory.create.side_effect = lambda *, bot_id: ResourceService(
        bot_id=bot_id, repository=repo
    )
    links = LinkWorkflow(factory, repo, Mock(), Mock())
    links._sync = Mock()
    bots = Mock()

    class Bindings(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=bots)
            binder.bind(LinkWorkflowProtocol, to=links)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    attach_injector(app, Injector([Bindings()]))
    mount_public_error_handlers(app)
    yield user_scoped_client(app, "u1"), repo, links
    engine.dispose()


@pytest.mark.parametrize("method", ["put", "delete"])
@pytest.mark.parametrize("resource_id", ["2", "3", "4", "5", str(1 << 63)])
def test_invisible_link_never_mutates_or_syncs(real_link_world, method, resource_id):
    client, repo, links = real_link_world
    before = repo.get_by_id(resource_id)
    kwargs = {"json": {"name": "changed"}} if method == "put" else {}
    response = getattr(client, method)(
        f"/openapi/v1/bots/b/links/{resource_id}", **kwargs
    )
    assert response.status_code == 404
    assert repo.get_by_id(resource_id) == before
    links._sync.assert_not_called()


@pytest.mark.parametrize("method", ["put", "delete"])
def test_invalid_id_through_real_workflow_preserves_data(real_link_world, method):
    client, repo, links = real_link_world
    before = repo.get_by_id("1")
    kwargs = {"json": {"name": "changed"}} if method == "put" else {}
    response = getattr(client, method)(
        "/openapi/v1/bots/b/links/not-a-number", **kwargs
    )
    assert response.status_code == 422
    assert repo.get_by_id("1") == before
    links._sync.assert_not_called()


def test_real_link_update_and_delete_keep_sync(real_link_world):
    client, repo, links = real_link_world
    response = client.put("/openapi/v1/bots/b/links/1", json={"name": "changed"})
    assert response.status_code == 200
    assert repo.get_by_id("1")["name"] == "changed"
    assert client.delete("/openapi/v1/bots/b/links/-1").status_code == 200
    assert repo.get_by_id("-1")["status"] == "deleted"
    assert links._sync.call_count == 2
