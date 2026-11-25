"""Real HTTP/DI/source/SQLite; only provider catalog is replaced at its port."""

import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from injector import InstanceProvider

from agentclaw.community.api.bot_knowledge_service import (
    BotKnowledgePublicationSelectionProtocol,
)
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel,
)
from agentclaw.community.core.knowledge_management.config import (
    KnowledgeManagementConfig,
)
from agentclaw.community.core.knowledge_management.ports import KnowledgeCatalog
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from tests.community.endpoints.test_bot_knowledge_router import (
    _BOT_ID,
    _KB_ID,
    _OWNER_ID,
    _seed_publication,
)


@pytest.fixture
def stage_http(app_with_testing_modules, world):
    _seed_publication(world)
    db = world.get(DatabasePlugin)
    with db.orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id=_BOT_ID).one()
        space_id = bot.space_id
    catalog = Mock(spec=KnowledgeCatalog)
    catalog.page_authorized.return_value = {"kb_ids": [_KB_ID], "next_cursor": None}
    catalog.details.return_value = [
        {
            "space_id": str(space_id),
            "kb_id": _KB_ID,
            "name": "Knowledge",
            "kgbase_status": "ready",
            "can_manage": True,
        }
    ]
    world.injector.binder.bind(KnowledgeCatalog, to=InstanceProvider(catalog))
    world.injector.binder.bind(
        KnowledgeManagementConfig,
        to=KnowledgeManagementConfig(enabled=True, space_tenant="teamclaw"),
    )
    observer = world.get(BotKnowledgePublicationSelectionProtocol)
    with TestClient(app_with_testing_modules) as client:
        yield client, observer, db, catalog


def project(observer, *, publication_id):
    observed = observer.observe_online_selection(publication_id=publication_id)
    return observer.project_online_selection(
        publication_id=publication_id, observed=observed
    )


def get(client, route, stage):
    return client.get(
        f"/api/knowledge/bots/{_BOT_ID}/{route}-knowledge-bases",
        params={"owner_id": _OWNER_ID, "runtime_stage": stage},
        headers={"x-user-id": _OWNER_ID},
    )


def test_existing_queries_share_stages_without_draft_fallback(stage_http):
    client, observer, db, catalog = stage_http
    assert observer.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        session.get(BotPublishModel, 101).status = "validating"
        session.query(BotKnowledgeBindingModel).update({"enabled": False})
    for route in ("available", "conversation"):
        response = get(client, route, "verify")
        assert response.status_code == 200, response.text
        assert response.json()["selection_status"] == "last_recorded"
        assert response.json()["items"][0]["kb_id"] == _KB_ID
    assert get(client, "available", "draft").json()["items"][0]["enabled"] is False
    with db.orm_session() as session:
        row = session.get(BotPublishModel, 101)
        row.status = "success"
        row.ext = json.dumps({"publish": {"online": 901}})
    missing = get(client, "available", "online").json()
    assert missing == {"items": [], "next_cursor": None}
    assert get(client, "conversation", "online").json()["items"] == []
    assert project(observer, publication_id=101)
    assert get(client, "available", "online").json()["items"][0]["enabled"] is True
    assert get(client, "conversation", "online").json()["items"] == [
        {"kb_id": _KB_ID, "name": "Knowledge"}
    ]
    # Current grants, not the persisted snapshot, remain authorization authority.
    catalog.page_authorized.return_value = {"kb_ids": [], "next_cursor": None}
    assert get(client, "conversation", "online").json()["items"] == []


def test_existing_route_rejects_invalid_stage_and_old_new_endpoint_is_removed(
    stage_http,
):
    client, _, _, _ = stage_http
    assert get(client, "available", "invalid").status_code == 422
    assert (
        client.get(
            f"/api/bot-knowledge/{_BOT_ID}/publications/101/selection",
            params={"owner_id": _OWNER_ID},
            headers={"x-user-id": _OWNER_ID},
        ).status_code
        == 404
    )


def test_queries_use_real_source_provider_and_do_not_write_on_read(stage_http):
    client, observer, db, _ = stage_http
    from sqlalchemy import event

    assert observer.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        session.get(BotPublishModel, 101).status = "validating"
        engine = session.get_bind()
    statements = []

    def record(conn, cursor, sql, *args):
        # Authentication/native Bot setup may complete unrelated background
        # writes during a full-suite run. Assert this feature's write boundary,
        # not a global ban on native startup/authentication persistence.
        if any(
            table in sql
            for table in (
                "ac_bot_common_config",
                "ac_bot_knowledge_binding",
                "ac_bot_publish",
            )
        ):
            statements.append(sql)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert (
            get(client, "conversation", "verify").json()["selection_status"]
            == "last_recorded"
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert any("ac_bot_common_config" in sql for sql in statements)


@pytest.mark.parametrize("route", ["available", "conversation"])
@pytest.mark.parametrize("stage", ["verify", "online"])
@pytest.mark.parametrize("failure", ["missing", "corrupt", "storage"])
def test_unknown_auxiliary_state_is_exact_empty_page_without_error_details(
    stage_http, route, stage, failure
):
    from agentclaw.community.plugin_api.models import BotCommonConfig

    client, observer, db, catalog = stage_http
    if failure != "missing":
        assert observer.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        publication = session.get(BotPublishModel, 101)
        publication.status = "validating" if stage == "verify" else "success"
        publication.ext = json.dumps({"publish": {"online": 901}})
    if stage == "online" and failure != "missing":
        assert project(observer, publication_id=101)
    if failure == "corrupt":
        with db.orm_session() as session:
            session.query(BotCommonConfig).update(
                {"config_value": "invalid snapshot: internal database detail"}
            )
    elif failure == "storage":
        # Hold the local DatabasePlugin session lock: engine-level DDL races
        # native background Sessions on SQLite's shared StaticPool connection.
        with db.orm_session() as session:
            BotCommonConfig.__table__.drop(session.connection())
    before = catalog.mock_calls[:]
    response = get(client, route, stage)
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "next_cursor": None}
    assert catalog.mock_calls == before  # No provider request for unknown state.
    with db.orm_session() as session:
        binding = session.query(BotKnowledgeBindingModel).one()
        assert binding.enabled is True  # Never turn unknown into a draft write.


def test_missing_snapshot_does_not_suppress_normal_source_authorization(stage_http):
    client, _, _, _ = stage_http
    response = client.get(
        f"/api/knowledge/bots/{_BOT_ID}/available-knowledge-bases",
        params={"owner_id": "not-the-owner", "runtime_stage": "online"},
        headers={"x-user-id": _OWNER_ID},
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("stage", ["verify", "online"])
@pytest.mark.parametrize("failure", ["missing", "corrupt", "storage", "recorded"])
def test_available_stage_preserves_space_admission_even_when_snapshot_unknown(
    stage_http, stage, failure
):
    from agentclaw.community.core.spaces.repository.models import (
        SpaceMemberModel,
        SpaceModel,
    )
    from agentclaw.community.plugin_api.models import BotCommonConfig

    client, observer, db, catalog = stage_http
    if failure != "missing":
        assert observer.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        publication = session.get(BotPublishModel, 101)
        publication.status = "validating" if stage == "verify" else "success"
        publication.ext = json.dumps({"publish": {"online": 901}})
    if stage == "online" and failure != "missing":
        assert project(observer, publication_id=101)
    with db.orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id=_BOT_ID).one()
        space = session.get(SpaceModel, bot.space_id)
        space.space_type = "PERSONAL"
        space.personal_owner_id = _OWNER_ID
        session.query(SpaceMemberModel).filter_by(
            space_id=space.id, user_id=_OWNER_ID
        ).delete()
        if failure == "corrupt":
            session.query(BotCommonConfig).update({"config_value": "invalid"})
        elif failure == "storage":
            BotCommonConfig.__table__.drop(session.connection())
    before = catalog.mock_calls[:]
    for requested_stage in ("draft", stage):
        response = get(client, "available", requested_stage)
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "SPACE_MEMBER_REQUIRED"
    assert catalog.mock_calls == before
