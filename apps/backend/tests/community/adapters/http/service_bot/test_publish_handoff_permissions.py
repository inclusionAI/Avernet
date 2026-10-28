"""Legacy publish commands authorize the current Owner, not resource locators."""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.service_bot.router_publish import upgrade_publish
from agentclaw.community.adapters.http.service_bot.schemas_publish import UpgradePublishRequest
from agentclaw.community.api.bot_publish_service import BotPublishServiceProtocol
from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
    CollaboratorLockService,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.bot.bot import (
    BotRepository as OrmBotRepository,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.plugin_api.models import BotModel


class Database:
    def __init__(self, path):
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def orm_session(self):
        with self.sessions.begin() as session:
            yield session

    transactional_orm_session = orm_session


@pytest.fixture
def publication(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "dev")
    database = Database(tmp_path / "publish.db")
    bots = OrmBotRepository(database)
    bot = bots.insert({
        "bot_id": "service", "entity_id": "A", "entity_type": "staff",
        "owner_id": "B", "creator_id": "A", "bot_type": "service",
        "active_engine": "moltis", "status": "ACTIVE",
    })
    record = SimpleNamespace(
        source_bot_pk=bot["id"], source_bot_id="service", owner_id="A"
    )
    service = Mock()
    service.get_publish_by_id.return_value = record
    service.upgrade_publish.return_value.to_dict.return_value = {
        "id": 13821, "source_bot_pk": bot["id"], "owner_id": "A"
    }
    locks = Mock()
    locks.get_lock_info.return_value = SimpleNamespace(has_collaborators=False)
    dependencies = {
        BotRepository: bots,
        BotPublishServiceProtocol: service,
        CollaboratorLockService: locks,
        CollaboratorService: Mock(),
    }
    app = FastAPI()
    app.state.injector = SimpleNamespace(get=lambda cls: dependencies[cls])
    request = Request({
        "type": "http", "method": "POST", "app": app,
        "path": "/api/service-bot/publish/upgrade", "headers": [],
    })
    yield SimpleNamespace(
        database=database, bots=bots, pk=bot["id"], service=service, record=record,
        locks=locks, request=request,
    )
    database.engine.dispose()


async def upgrade(world, actor):
    return await upgrade_publish(
        request=UpgradePublishRequest(publish_id=13820),
        user=AuthenticatedUser(id=actor, staffId=actor, operatorName=actor),
        publish_service=world.service,
        __interceptor_request__=world.request,
    )


@pytest.mark.asyncio
async def test_new_owner_can_upgrade_using_unchanged_legacy_publish_id(publication):
    response = await upgrade(publication, "B")

    assert response.success is True
    assert response.data["owner_id"] == "A"
    assert publication.record.owner_id == "A"
    publication.service.upgrade_publish.assert_called_once_with(
        publish_id=13820, owner_id="B"
    )
    assert publication.locks.get_lock_info.call_args.kwargs["owner_id"] == "B"


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["A", "outsider"])
async def test_resource_locator_does_not_grant_owner_permission(publication, actor):
    response = await upgrade(publication, actor)

    assert response.status_code == 403
    assert json.loads(response.body)["error_code"] == 403
    publication.service.upgrade_publish.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["B", "A", "outsider"])
@pytest.mark.parametrize("publish_locator", ["A", "staff_A"])
async def test_legacy_owner_and_entity_publish_rows_authorize_by_source_pk(publication, actor, publish_locator):
    with publication.database.orm_session() as session:
        session.get(BotModel, publication.pk).entity_id = "staff_A"
    publication.record.owner_id = publish_locator

    response = await upgrade(publication, actor)

    if actor == "B":
        assert response.success is True
        publication.service.upgrade_publish.assert_called_once_with(publish_id=13820, owner_id="B")
        assert publication.locks.get_lock_info.call_args.kwargs["owner_id"] == "B"
    else:
        assert response.status_code == 403
        publication.service.upgrade_publish.assert_not_called()
    assert publication.record.owner_id == publish_locator


@pytest.mark.asyncio
async def test_owner_equal_to_entity_keeps_existing_upgrade_behavior(publication):
    with publication.database.orm_session() as session:
        session.get(BotModel, publication.pk).owner_id = "A"

    response = await upgrade(publication, "A")

    assert response.success is True
    publication.service.upgrade_publish.assert_called_once_with(
        publish_id=13820, owner_id="A"
    )


@pytest.mark.asyncio
async def test_handoff_maintenance_still_blocks_new_owner(publication):
    with publication.database.orm_session() as session:
        session.get(BotModel, publication.pk).ext = json.dumps({"owner_handoff": "pending"})

    response = await upgrade(publication, "B")

    assert response.status_code == 423
    assert json.loads(response.body)["message"] == "BOT_OWNER_HANDOFF_IN_PROGRESS"
    publication.service.upgrade_publish.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [
    "process_publish", "delete_service_bot", "offline_publish", "restart_publish",
    "restart_publish_in_place", "retry_publish", "restore_draft", "rollback_publish",
    "scale_publish_bot", "update_publish_status",
])
@pytest.mark.parametrize("actor", ["B", "A", "outsider"])
async def test_other_publish_commands_authorize_current_owner(publication, action, actor):
    from unittest.mock import AsyncMock
    from agentclaw.community.adapters.http.service_bot import router_publish as routes
    from agentclaw.community.adapters.http.service_bot.schemas_publish import PublishFlowRequest
    from agentclaw.community.core.service_bot.repository.models import PublishStatus

    service = publication.service
    publication.record.ext = {}
    result = SimpleNamespace(
        status=PublishStatus.BUILT, message="accepted", model_dump=lambda: {"accepted": True},
    )
    methods = {
        "process_publish": ("process", AsyncMock(return_value=result)),
        "delete_service_bot": ("delete_service_bot", Mock(return_value=True)),
        "offline_publish": ("offline_publish", AsyncMock(return_value={"success": True})),
        "restart_publish": ("restart_bot", Mock(return_value={"success": True})),
        "restart_publish_in_place": ("restart_bot", Mock(return_value={"success": True})),
        "retry_publish": ("retry", AsyncMock(return_value=result)),
        "restore_draft": ("restore_draft", AsyncMock(return_value={})),
        "rollback_publish": ("rollback_publish", AsyncMock(return_value={})),
        "scale_publish_bot": ("scale_bot", AsyncMock(return_value={"success": True})),
        "update_publish_status": ("update_publish_status_with_ext", Mock(return_value=SimpleNamespace(to_dict=lambda: {}))),
    }
    method_name, command = methods[action]
    setattr(service, method_name, command)
    kwargs = {
        "user": AuthenticatedUser(id=actor, staffId=actor, operatorName=actor),
        "__interceptor_request__": publication.request,
    }
    if action == "process_publish":
        kwargs["request"] = PublishFlowRequest(publish_id=13820)
    else:
        kwargs["publish_id"] = 13820
    if action == "update_publish_status":
        from agentclaw.community.adapters.http.service_bot.schemas_publish import UpdatePublishStatusRequest
        kwargs["request"] = UpdatePublishStatusRequest(
            source_status="building", target_status="failed", failed_status="building",
        )
    if action in {"process_publish", "restart_publish", "restart_publish_in_place", "retry_publish", "scale_publish_bot"}:
        kwargs["flow_service"] = service
    else:
        kwargs["publish_service"] = service

    response = await getattr(routes, action)(**kwargs)

    if actor == "B":
        assert response.success is True
        command.assert_called_once()
        assert publication.locks.get_lock_info.call_args.kwargs["owner_id"] == "B"
    else:
        assert response.status_code == 403
        command.assert_not_called()
    assert publication.record.owner_id == "A"


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["B", "A", "outsider"])
@pytest.mark.parametrize("maintenance", [False, True])
async def test_read_preflight_uses_current_owner_without_maintenance_write_fence(
    publication, actor, maintenance,
):
    from agentclaw.community.adapters.http.service_bot.router_publish import can_restore_draft

    publication.request.scope["method"] = "GET"
    publication.service.can_restore_draft.return_value = (True, "ready", None)
    if maintenance:
        with publication.database.orm_session() as session:
            session.get(BotModel, publication.pk).ext = json.dumps({"owner_handoff": "pending"})

    response = await can_restore_draft(
        publish_id=13820,
        user=AuthenticatedUser(id=actor, staffId=actor, operatorName=actor),
        publish_service=publication.service,
        __interceptor_request__=publication.request,
    )

    if actor == "B":
        assert response.success is True
        assert response.data["can_restore_draft"] is True
    else:
        assert response.status_code == 403
        publication.service.can_restore_draft.assert_not_called()


@pytest.mark.asyncio
async def test_existing_admin_collaborator_keeps_upgrade_permission_after_handoff(publication):
    publication.locks.get_lock_info.return_value = SimpleNamespace(
        has_collaborators=True, lock=SimpleNamespace(holder_user_id="collaborator"),
        holder_name="Collaborator", is_owner=False,
    )
    collaborators = publication.request.app.state.injector.get(CollaboratorService)
    collaborators.check_collaborator_permission.return_value = {
        "has_permission": True, "level": "ADMIN",
    }

    response = await upgrade(publication, "collaborator")

    assert response.success is True
    publication.service.upgrade_publish.assert_called_once_with(
        publish_id=13820, owner_id="collaborator",
    )
    permission = collaborators.check_collaborator_permission.call_args.kwargs
    assert permission["owner_id"] == "B"
    assert permission["user_id"] == "collaborator"
    assert publication.record.owner_id == "A"
