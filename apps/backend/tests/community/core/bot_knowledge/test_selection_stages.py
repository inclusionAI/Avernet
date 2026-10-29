"""Draft/verification and production selections are independent."""

import pytest
from tests.community.core.bot_knowledge.summary_fixtures import current_row, seed_summary
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel as Binding,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.models import BotModel


def seed_service(db):
    with db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 1).update({"bot_type": "service"})
        session.add(
            BotPublishModel(
                source_bot_pk=1,
                source_bot_id="bot",
                publish_bot_id="bot-pub-1",
                owner_id="owner",
                name="Bot",
                status="draft",
                version=1,
                env="dev",
                permission_owner="owner",
            )
        )
        session.add_all(
            [
                Binding(
                    env="dev",
                    bot_pk=1,
                    space_id=1,
                    kb_id="a",
                    enabled=False,
                    online_enabled=True,
                    revision=3,
                    updated_by="owner",
                ),
                Binding(
                    env="dev",
                    bot_pk=1,
                    space_id=1,
                    kb_id="b",
                    enabled=True,
                    online_enabled=False,
                    revision=2,
                    updated_by="owner",
                ),
            ]
        )

        session.flush()
        seed_summary(session, session.get(BotModel, 1),
                     session.query(BotPublishModel).one(), ["a"], workflow=100)


def test_service_stage_reads_are_independent(knowledge):
    db, repo, service, scope = knowledge
    seed_service(db)
    assert repo.list_enabled_current(scope) == ("b",)
    assert service.list_enabled(scope, selection_stage="draft") == ("b",)
    assert service.list_enabled(scope, selection_stage="online") == ("a",)
    draft = service.list_bindings(scope=scope, actor_id="owner")
    online = service.list_bindings(
        scope=scope, actor_id="owner", selection_stage="online"
    )
    assert draft["selection_stage"] == "draft"
    assert online["selection_stage"] == "online"
    assert {r["kb_id"]: r["enabled"] for r in online["items"]} == {
        "a": True,
        "b": False,
    }
    assert all(r["runtime_selection"] == "online" for r in online["items"])


def test_draft_mutation_does_not_touch_online_or_production_cas(knowledge):
    db, _, service, scope = knowledge
    seed_service(db)
    service.set_enabled(
        scope=scope, actor_id="owner", kb_id="b", enabled=False, expected_revision=2
    )
    assert service.list_enabled(scope, selection_stage="draft") == ()
    assert service.list_enabled(scope, selection_stage="online") == ("a",)
    with db.orm_session() as session:
        assert session.query(Binding).filter_by(kb_id="a").one().revision == 3


def test_personal_online_read_remains_current(knowledge):
    _, _, service, scope = knowledge
    service.set_enabled(
        scope=scope, actor_id="owner", kb_id="kb", enabled=True, expected_revision=0
    )
    assert service.list_enabled(scope, selection_stage="online") == ("kb",)
    result = service.list_bindings(
        scope=scope, actor_id="owner", selection_stage="online"
    )
    assert result["items"][0]["enabled"] is True
    assert result["items"][0]["runtime_selection"] == "current"


@pytest.mark.parametrize("stage", ["verify", "current", "", None, "ONLINE"])
def test_invalid_stage_is_rejected(knowledge, stage):
    _, _, service, scope = knowledge
    with pytest.raises(ValueError, match="selection_stage"):
        service.list_enabled(scope, selection_stage=stage)
    with pytest.raises(ValueError, match="selection_stage"):
        service.list_bindings(scope=scope, actor_id="owner", selection_stage=stage)


def test_service_missing_production_is_not_draft_fallback(knowledge):
    db, _, service, scope = knowledge
    seed_service(db)
    with db.orm_session() as session:
        session.query(Binding).update({"online_enabled": False})
    assert service.list_enabled(scope, selection_stage="online") == ()
    assert service.list_enabled(scope, selection_stage="draft") == ("b",)


def test_reclaimed_space_row_cannot_inherit_old_production_selection(knowledge):
    from agentclaw.community.core.spaces.repository.models import (
        SpaceMemberModel,
        SpaceModel,
    )

    db, _, service, scope = knowledge
    seed_service(db)
    with db.orm_session() as session:
        session.add(
            SpaceModel(
                id=2,
                space_code="new-team",
                space_type="TEAM",
                name="New",
                env="dev",
                created_by="owner",
                updated_by="owner",
            )
        )
        session.add(
            SpaceMemberModel(
                space_id=2,
                user_id="owner",
                role="MEMBER",
                env="dev",
                created_by="owner",
            )
        )
        session.query(BotModel).filter(BotModel.id == 1).update({"space_id": 2})
    assert service.list_enabled(scope, selection_stage="online") == ()
    service.catalog.authorized.add("a")
    result = service.set_enabled(
        scope=scope, actor_id="owner", kb_id="a", enabled=True, expected_revision=0
    )
    assert result["revision"] == 4
    assert service.list_enabled(scope, selection_stage="draft") == ("a",)
    assert service.list_enabled(scope, selection_stage="online") == ()


def test_online_selection_preserves_owner_tenant_env_and_space_boundaries(knowledge):
    from agentclaw.community.kernel.knowledge import BotKnowledgeScope
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    db, _, service, scope = knowledge
    seed_service(db)
    with db.orm_session() as session:
        session.add_all(
            [
                Binding(
                    env="prod",
                    bot_pk=1,
                    space_id=1,
                    kb_id="foreign-env",
                    enabled=False,
                    online_enabled=True,
                    revision=1,
                    updated_by="owner",
                ),
                Binding(
                    env="dev",
                    bot_pk=1,
                    space_id=2,
                    kb_id="foreign-space",
                    enabled=False,
                    online_enabled=True,
                    revision=1,
                    updated_by="owner",
                ),
            ]
        )
    with avernet_tenant_scope("other-tenant"), db.orm_session() as session:
        session.add(
            Binding(
                env="dev",
                bot_pk=1,
                space_id=1,
                kb_id="foreign-tenant",
                enabled=False,
                online_enabled=True,
                revision=1,
                updated_by="owner",
            )
        )
    assert service.list_enabled(scope, selection_stage="online") == ("a",)
    assert (
        service.list_enabled(
            BotKnowledgeScope("bot", "other", "dev"), selection_stage="online"
        )
        == ()
    )
    with avernet_tenant_scope("other-tenant"):
        assert service.list_enabled(scope, selection_stage="online") == ()


@pytest.mark.parametrize(
    "status", ["building", "built", "validate_pub", "validating", "online_pub"]
)
def test_service_cannot_edit_shared_working_selection_during_release(knowledge, status):
    from agentclaw.community.kernel.knowledge import KnowledgeError

    db, _, service, scope = knowledge
    seed_service(db)
    with db.orm_session() as session:
        session.query(BotPublishModel).filter_by(source_bot_pk=1).update(
            {"status": status}
        )
    with pytest.raises(KnowledgeError, match="BOT_DRAFT_REQUIRED"):
        service.set_enabled(
            scope=scope, actor_id="owner", kb_id="b", enabled=False, expected_revision=2
        )
    assert service.list_enabled(scope, selection_stage="draft") == ("b",)
    assert service.list_enabled(scope, selection_stage="online") == ("a",)


def test_unknown_completion_is_denied_in_the_same_statement_as_online_flags(knowledge):
    import json
    from sqlalchemy import event

    db, repo, service, scope = knowledge
    seed_service(db)
    with db.orm_session() as session:
        session.query(BotPublishModel).update({
            "status": "success",
            "ext": json.dumps({"knowledge_selection_result": {
                "status": "unknown", "reason": "KNOWLEDGE_SNAPSHOT_MISSING",
            }}),
        })
    with db.orm_session() as session:
        row = current_row(session)
        value = json.loads(row.config_value)
        value.update(status="unknown", enabled_kb_ids=[])
        row.config_value = json.dumps(value)
    statements = []
    engine = db.factory.kw["bind"]

    def record(_conn, _cursor, sql, *_args):
        statements.append(sql)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert repo.list_enabled(scope, selection_stage="online") == ()
        result = service.list_bindings(scope=scope, actor_id="owner", selection_stage="online")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert not any(item["enabled"] for item in result["items"])
    assert repo.list_enabled_current(scope) == ("b",)
    # UNKNOWN exits the enabled-ID path early. The page still returns its
    # ordinary bounded rows, with a constant false selection gate.
    flag_reads = [sql for sql in statements if "FROM ac_bot_knowledge_binding" in sql]
    assert len(flag_reads) == 1
    assert all("ac_bot_publish" not in sql and "JSON_EXTRACT" not in sql for sql in statements)


@pytest.mark.parametrize("payload,denied", [
    ({"knowledge_selection_result": {"status": "applied"},
      "config_artifact": {"metadata": {"knowledge_selection_result": {"status": "unknown"}}}}, False),
    ({"knowledge-selection-result": {"status": "unknown"}}, False),
    ({"status": "unknown", "reason": "legacy"}, True),
])
def test_only_structured_top_level_unknown_denies_online_selection(knowledge, payload, denied):
    import json

    db, repo, service, scope = knowledge
    seed_service(db)
    with db.orm_session() as session:
        session.query(BotPublishModel).update({
            "status": "success", "ext": json.dumps(payload, separators=(",", ":")),
        })
    with db.orm_session() as session:
        row = current_row(session)
        value = json.loads(row.config_value)
        value.update(payload)
        row.config_value = json.dumps(value, separators=(",", ":"))
    assert repo.list_enabled(scope, selection_stage="online") == (() if denied else ("a",))
    result = service.list_bindings(scope=scope, actor_id="owner", selection_stage="online")
    flags = {item["kb_id"]: item["enabled"] for item in result["items"]}
    assert flags == {"a": not denied, "b": False}


@pytest.mark.parametrize("latest_state", ["applied", "unknown"])
def test_owner_handoff_back_does_not_resurrect_older_completion(knowledge, latest_state):
    import json

    db, repo, _service, scope = knowledge
    seed_service(db)
    with db.orm_session() as session:
        session.query(BotPublishModel).update({
            "status": "upgraded", "ext": json.dumps({"knowledge_selection_result": {
                "status": "applied", "deployment_id": 100, "owner_id": "owner",
            }}),
        })
        session.add(BotPublishModel(
            source_bot_pk=1, source_bot_id="bot", publish_bot_id="bot-pub-1",
            owner_id="owner", name="Bot", status="draft", version=2,
            env="dev", permission_owner="owner",
            ext=json.dumps({"knowledge_selection_result": {
                "status": latest_state, "deployment_id": 200, "owner_id": "other",
            }}),
        ))
    with db.orm_session() as session:
        row = current_row(session)
        value = json.loads(row.config_value)
        value.update(owner_id="other", deployment_id=200, status=latest_state)
        row.config_value = json.dumps(value)
    # Current Owner A must not select A's old100 over actual latest B200.
    assert repo.list_enabled(scope, selection_stage="online") == ()
