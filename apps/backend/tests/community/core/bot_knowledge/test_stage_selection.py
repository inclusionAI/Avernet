"""Stage selection isolation uses real persisted rows, not private reader mocks."""

import json

import pytest
from sqlalchemy import event

from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel as Binding,
)
from agentclaw.community.core.bot_knowledge.stage_selection import (
    BotKnowledgeStageSelection,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel as Publication,
)
from agentclaw.community.plugin_api.models import BotCommonConfig, BotModel


def source(db):
    with db.orm_session() as session:
        return session.get(BotModel, 1).to_dict()


def terminal(db, publication_id=101, workflow_id=301):
    with db.orm_session() as session:
        publication = session.get(Publication, publication_id)
        publication.status = "success"
        publication.ext = json.dumps({"publish": {"online": workflow_id}})


def test_capture_then_verify_then_online_projection_does_not_change_draft_cas(
    observation,
):
    db, observer, _ = observation
    reader = BotKnowledgeStageSelection(db)
    assert observer.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        session.get(Publication, 101).status = "validating"
        row = session.query(Binding).one()
        row.enabled = False  # New draft/edit must never be substituted into verify.
        before = (row.revision, row.updated_by)
    assert reader.read(bot=source(db), runtime_stage="verify")["kb_ids"] == ["kb"]
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] is None
    terminal(db)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] is None
    assert observer.project_online_selection(publication_id=101)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] == ["kb"]
    with db.orm_session() as session:
        row = session.query(Binding).one()
        assert row.online_enabled is True
        assert row.enabled is False
        assert (row.revision, row.updated_by) == before


def test_rollback_target_snapshot_restores_only_online_and_preserves_history(
    observation,
):
    db, observer, _ = observation
    reader = BotKnowledgeStageSelection(db)
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    assert observer.project_online_selection(publication_id=101)
    with db.orm_session() as session:
        session.get(Publication, 101).status = "upgraded"
        session.add(
            Publication(
                id=102,
                source_bot_pk=1,
                source_bot_id="bot",
                publish_bot_id="botpub2",
                name="Bot",
                owner_id="owner",
                status="building",
                version=2,
                last_pub_id=101,
                env="dev",
                permission_owner="owner",
            )
        )
        session.query(Binding).update({"enabled": False})
    assert observer.record_build_selection(publication_id=102)
    terminal(db, 102, 302)
    assert observer.project_online_selection(publication_id=102)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] == []
    with db.orm_session() as session:
        session.get(Publication, 102).status = "draft"
        session.get(Publication, 101).status = "online_pub"
    assert not observer.project_online_selection(publication_id=101)
    terminal(db, 101, 303)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] is None
    assert observer.project_online_selection(publication_id=101)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] == ["kb"]
    assert not observer.project_online_selection(publication_id=102)
    with db.orm_session() as session:
        assert session.query(BotCommonConfig).count() == 3  # two snapshots + one marker
        assert session.query(Binding).one().enabled is False


def test_history_missing_is_unknown_even_with_enabled_draft_and_default_false_online(
    observation,
):
    db, observer, _ = observation
    terminal(db)
    assert not observer.project_online_selection(publication_id=101)
    result = BotKnowledgeStageSelection(db).read(bot=source(db), runtime_stage="online")
    assert result["selection_status"] == "unrecorded" and result["kb_ids"] is None


@pytest.mark.parametrize("raw", ["not json", "{}", "null", '"text"'])
def test_corrupt_history_never_falls_back_to_draft(observation, raw):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        session.query(BotCommonConfig).one().config_value = raw
        session.get(Publication, 101).status = "validating"
    result = BotKnowledgeStageSelection(db).read(bot=source(db), runtime_stage="verify")
    assert result["selection_status"] == "unrecorded" and result["kb_ids"] is None


def test_snapshot_store_failure_is_unavailable_not_disabled(observation):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        session.get(Publication, 101).status = "validating"
    BotCommonConfig.__table__.drop(db.factory.kw["bind"])
    assert (
        BotKnowledgeStageSelection(db).read(bot=source(db), runtime_stage="verify")[
            "selection_status"
        ]
        == "unavailable"
    )


def test_projection_write_failure_rolls_back_auxiliary_rows_and_native_success_stays(
    observation,
):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    engine = db.factory.kw["bind"]

    def fail_marker(conn, cursor, statement, *args):
        if "INSERT INTO ac_bot_common_config" in statement:
            raise RuntimeError("projection store failed")

    event.listen(engine, "before_cursor_execute", fail_marker)
    try:
        assert not observer.project_online_selection(publication_id=101)
    finally:
        event.remove(engine, "before_cursor_execute", fail_marker)
    with db.orm_session() as session:
        assert session.get(Publication, 101).status == "success"
        assert session.query(Binding).one().online_enabled is False
        assert session.query(Binding).one().enabled is True


def test_late_projection_cannot_overwrite_later_workflow_marker(observation):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    terminal(db, workflow_id=400)
    assert observer.project_online_selection(publication_id=101)
    terminal(db, workflow_id=399)
    assert not observer.project_online_selection(publication_id=101)
    assert (
        BotKnowledgeStageSelection(db).read(bot=source(db), runtime_stage="online")[
            "kb_ids"
        ]
        is None
    )


def test_new_draft_coexists_with_online_but_not_with_verify(observation):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    assert observer.project_online_selection(publication_id=101)
    with db.orm_session() as session:
        session.add(
            Publication(
                id=102,
                source_bot_pk=1,
                source_bot_id="bot",
                publish_bot_id="botpub2",
                name="Bot",
                owner_id="owner",
                status="draft",
                version=2,
                env="dev",
                permission_owner="owner",
            )
        )
    reader = BotKnowledgeStageSelection(db)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] == ["kb"]
    assert reader.read(bot=source(db), runtime_stage="verify")["kb_ids"] is None


def test_online_projection_is_independent_of_later_same_publication_build_sample(
    observation,
):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    assert observer.project_online_selection(publication_id=101)
    with db.orm_session() as session:
        row = (
            session.query(BotCommonConfig)
            .filter(BotCommonConfig.config_key == "publication:101")
            .one()
        )
        value = json.loads(row.config_value)
        value["kb_ids"] = []
        row.config_value = json.dumps(value)
    assert BotKnowledgeStageSelection(db).read(bot=source(db), runtime_stage="online")[
        "kb_ids"
    ] == ["kb"]
