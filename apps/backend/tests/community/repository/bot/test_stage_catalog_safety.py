"""Malformed-data isolation and bounded SQL stage selection regressions."""

from datetime import datetime, timedelta

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Query

from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.core.repository.implementations.bot.stage_catalog import apply_stage_filter
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from agentclaw.community.core.engine_runtime.stage import published_stage_binding
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from tests.community.repository.bot.test_stage_catalog import catalog as catalog, bot, publish
from tests.community.adapters.http.openapi_v1.test_catalog_stages import catalog_client as catalog_client
from tests.community.adapters.http.openapi_v1.test_collaborating_bots import _client, _relation


@pytest.mark.parametrize("raw", [
    "{", "[]", '{"binding":[1]}', '{"binding":{"online":{}}}',
    '{"binding":{"online":"71bad"}}', '{"binding":{"online":true}}',
    '{"binding":{"online":1.5}}', '{"binding":{"online":null}}',
])
def test_bad_latest_publication_keeps_other_metadata_and_never_falls_back(catalog, raw):
    repo, db = catalog
    good = bot(repo, "good")
    bad = bot(repo, "bad")
    bot(repo, "personal", kind="personal", binding=None)
    publish(db, good, bindings={"online": 71}, version=3)
    publish(db, bad, bindings={"online": 72})
    publish(db, bad, version=2)
    with db.orm_session() as session:
        session.query(BotPublishModel).filter(
            BotPublishModel.source_bot_pk == bad["id"], BotPublishModel.version == 2,
        ).update({BotPublishModel.ext: raw})
    total, rows = repo.list_bots_by_owner_bot_pairs([
        (name, "owner") for name in ("good", "bad", "personal")
    ])
    by_id = {row["bot_id"]: row for row in rows}
    assert total == 3
    assert by_id["good"]["online_available"] is True
    assert by_id["good"]["live_version"] == 3
    assert by_id["bad"]["online_available"] is False
    assert by_id["bad"]["live_version"] is None
    assert by_id["personal"]["online_available"] is None
    total, rows = repo.list_by_conditions(owner_id="owner", stage="online")
    assert total == 2
    assert {row["bot_id"] for row in rows} == {"good", "personal"}


@pytest.mark.parametrize("stage", ["online", "verify"])
def test_filter_only_materializes_requested_page_not_all_bots_or_history(catalog, monkeypatch, stage):
    repo, db = catalog
    sources = [bot(repo, f"service-{index}") for index in range(8)]
    with db.orm_session() as session:
        session.add_all([
            BotPublishModel(
                source_bot_pk=source["id"], source_bot_id=source["bot_id"],
                publish_bot_id=source["bot_id"], name="Service", owner_id="owner",
                permission_owner="owner", env=get_current_env(),
                status="success" if stage == "online" else "validating", version=version,
                ext='{"binding":{"online":71,"verify":72}}',
                gmt_create=datetime(2026, 10, 1) + timedelta(days=version),
            ) for source in sources for version in range(1, 21)
        ])
    sizes = []
    original = Query.all

    def traced_all(query):
        rows = original(query)
        sizes.append(len(rows))
        return rows

    monkeypatch.setattr(Query, "all", traced_all)
    total, rows = repo.list_by_conditions(owner_id="owner", stage=stage, page=2, page_size=2)
    assert total == 8
    assert len(rows) == 2
    assert sizes == [2], "COUNT/stage predicates must not load candidate Bots or publication history"
    sizes.clear()
    total, metadata = repo.list_bots_by_owner_bot_pairs(
        [(source["bot_id"], "owner") for source in sources], page=2, page_size=2,
    )
    assert total == 8 and len(metadata) == 2
    assert sizes == ([2, 2] if stage == "online" else [2, 0])


def test_omitted_stage_http_defaults_to_draft_and_keeps_personal_bot(catalog, catalog_client):
    repo, db = catalog
    bot(repo, "draft")
    online_only = bot(repo, "online-only", binding=None)
    publish(db, online_only, bindings={"online": 71})
    bot(repo, "personal", kind="personal", binding=None)
    catalog_client.repository.list_by_conditions.side_effect = repo.list_by_conditions
    response = catalog_client.client.get("/openapi/v1/bots")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["total"] == 2
    assert {row["bot_id"] for row in response.json()["data"]["items"]} == {"draft", "personal"}
    response = catalog_client.client.get("/openapi/v1/bots?stage=online")
    assert response.status_code == 200, response.text
    assert {row["bot_id"] for row in response.json()["data"]["items"]} == {"online-only", "personal"}


def test_metadata_http_survives_malformed_publish_record(catalog, catalog_client):
    repo, db = catalog
    good, bad = bot(repo, "good"), bot(repo, "bad")
    publish(db, good, bindings={"online": 71})
    publish(db, bad)
    with db.orm_session() as session:
        session.query(BotPublishModel).filter(BotPublishModel.source_bot_pk == bad["id"]).update(
            {BotPublishModel.ext: '{"binding":{"online":"invalid-id"}}'},
        )

    def metadata(**kwargs):
        total, items = repo.list_bots_by_owner_bot_pairs(**kwargs)
        return {"total": total, "items": items}

    catalog_client.service.list_bots_by_owner_bot_pairs.side_effect = metadata
    response = catalog_client.client.post("/openapi/v1/bots/metadata/queries", json={
        "bots": [{"bot_id": name, "owner_id": "owner"} for name in ("good", "bad")],
    })
    assert response.status_code == 200, response.text
    rows = {row["bot_id"]: row for row in response.json()["data"]["items"]}
    assert rows["good"]["online_available"] is True
    assert rows["bad"]["online_available"] is False


def test_collaboration_http_defaults_to_draft_and_keeps_personal_bot(catalog):
    repo, db = catalog
    draft = bot(repo, "draft")
    online = bot(repo, "online-only", binding=None)
    personal = bot(repo, "personal", kind="personal", binding=None)
    publish(db, online, bindings={"online": 71})
    bots, collaborators = MagicMock(), MagicMock()
    bots.list_bots_by_owner_bot_pairs.return_value = {"total": 0, "items": []}
    collaborators.list_user_collaborations.return_value = [
        _relation(bot_id=row["bot_id"], owner_id="owner").model_copy(update={"bot_pk": row["id"]})
        for row in (draft, online, personal)
    ]
    client = _client(bots=bots, collaborators=collaborators)
    bots.page_live_bots_by_pks.side_effect = repo.page_live_bots_by_pks
    for suffix, names in (("", {"draft", "personal"}), ("?stage=online", {"online-only", "personal"})):
        response = client.get("/openapi/v1/bots/collaborations" + suffix)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["total"] == 2
        assert {row["bot_id"] for row in response.json()["data"]["items"]} == names


@pytest.mark.parametrize("stage", ["online", "verify"])
def test_mysql_predicates_compile_guarded_json_and_correlated_latest_publication(stage):
    dialect = mysql.dialect()
    session = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=dialect))
    query = apply_stage_filter(session, Query(BotModel), BotModel, stage)
    sql = str(query.statement.compile(dialect=dialect))
    assert "json_valid" in sql and "json_unquote" in sql and "REGEXP" in sql
    assert "EXISTS" in sql and "LIMIT" in sql
    assert "gmt_create DESC" in sql and "id DESC" in sql


def test_equal_timestamps_use_newest_id_without_fallback(catalog):
    repo, db = catalog
    source = bot(repo, "same-timestamp")
    publish(db, source, bindings={"online": 71})
    publish(db, source, version=2)
    with db.orm_session() as session:
        session.query(BotPublishModel).update({BotPublishModel.gmt_create: datetime(2026, 10, 1)})
    assert repo.list_by_conditions(owner_id="owner", stage="online")[0] == 0
    rows = repo.list_bots_by_owner_bot_pairs([("same-timestamp", "owner")])[1]
    assert rows[0]["online_available"] is False


@pytest.mark.parametrize("stage", ["online", "verify"])
@pytest.mark.parametrize("releases", [
    [],
    [("success", {"online": 71, "verify": 71})],
    [("success", {"online": "0071", "verify": " 71 "})],
    [("success", {"online": 71, "verify": 72})],
    [("success", {"online": 71, "verify": 71}), ("draft", {})],
    [("success", {"online": 71, "verify": 71}), ("validating", {})],
    [("success", {"online": 71, "verify": 71}), ("success", {})],
    [("success", {"online": 71, "verify": 71}), ("validating", {"verify": 73})],
    [("validating", {"verify": 73}), ("success", {"online": 71, "verify": 71})],
    [("success", {"online": 71, "verify": 71}), ("failed", {"online": 73})],
])
def test_sql_stage_selection_matches_runtime_policy(catalog, stage, releases):
    repo, db = catalog
    source = bot(repo, "service")
    bot(repo, "personal", kind="personal", binding=None)
    for version, (status, bindings) in enumerate(releases, 1):
        publish(db, source, status=status, bindings=bindings, version=version)
    with db.orm_session() as session:
        session.add_all([
            EntityDeviceBinding(
                id=identifier, entity_id="owner", entity_type="staff", device_id=str(identifier),
                device_provider="arca", env=get_current_env(), status=status, applied_by="owner",
            ) for identifier, status in ((71, "ACTIVE"), (72, "RELEASED"))
        ])
        records = [SimpleNamespace(id=row.id, status=row.status, ext=row.ext) for row in session.query(
            BotPublishModel,
        ).order_by(BotPublishModel.gmt_create.desc(), BotPublishModel.id.desc()).all()]
    try:
        published_stage_binding(
            records, bot_id="service", stage=stage, env=get_current_env(),
            binding_is_active=lambda identifier: identifier == 71,
        )
        expected = 2
    except EngineStageNotLiveError:
        expected = 1
    total, _ = repo.list_by_conditions(owner_id="owner", stage=stage)
    assert total == expected


def test_stage_and_metadata_queries_keep_tenant_and_environment_scope(catalog):
    repo, db = catalog
    with avernet_tenant_scope("tenant-a"):
        source = bot(repo, "default")
        publish(db, source, bindings={"online": 71})
        publish(db, source, version=2, env="other-environment")
    with avernet_tenant_scope("tenant-b"):
        bot(repo, "default")
        assert repo.list_by_conditions(owner_id="owner", stage="online")[0] == 0
        rows = repo.list_bots_by_owner_bot_pairs([("default", "owner")])[1]
        assert rows[0]["online_available"] is False
    with avernet_tenant_scope("tenant-a"):
        assert repo.list_by_conditions(owner_id="owner", stage="online")[0] == 1
        rows = repo.list_bots_by_owner_bot_pairs([("default", "owner")])[1]
        assert rows[0]["online_available"] is True
        assert rows[0]["live_version"] == 1
