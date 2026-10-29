"""Version observations are optional display data, not publication authority."""

import json
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_knowledge.publication_selection import (
    BotKnowledgePublicationSelection,
)
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel,
)
from agentclaw.community.core.repository.implementations.config.bot_common_config import (
    BotCommonConfigRepository,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel,
    PublishStatus,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.plugin_api.models import BotCommonConfig, BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


@pytest.fixture
def observation(knowledge):
    db, _, current, scope = knowledge
    current.catalog.authorized = ("kb", "kb-b")
    # Seed with the normal current selection path before becoming a service Bot.
    current.set_enabled(
        scope=scope, actor_id="owner", kb_id="kb", enabled=True, expected_revision=0
    )
    with db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 1).update({"bot_type": "service"})
        session.add(
            BotPublishModel(
                id=101,
                source_bot_pk=1,
                source_bot_id="bot",
                publish_bot_id="botpub1",
                name="Bot",
                owner_id="legacy-owner",
                status=PublishStatus.BUILDING,
                version=1,
                env="dev",
                permission_owner="owner",
            )
        )
    return (
        db,
        BotKnowledgePublicationSelection(db, BotCommonConfigRepository(db)),
        scope,
    )


def read(observation, **kwargs):
    _, service, scope = observation
    return service.get_publication_selection(
        scope=scope, actor_id="owner", publication_id=101, **kwargs
    )


def test_record_read_and_new_draft_choice_do_not_rewrite_version(observation):
    db, service, _ = observation
    assert service.record_build_selection(publication_id=101)
    original = read(observation)
    assert original["kb_ids"] == ["kb"]
    assert original["observation_status"] == "last_recorded"
    with db.orm_session() as session:
        session.query(BotPublishModel).filter_by(id=101).update({"status": "success"})
        session.query(BotKnowledgeBindingModel).update({"enabled": False})
    assert read(observation) == original
    with db.orm_session() as session:
        assert session.query(BotCommonConfig).count() == 1
        assert (
            session.query(BotPublishModel).filter_by(id=101).one().status == "success"
        )


def test_empty_is_recorded_but_absence_is_not(observation):
    db, service, _ = observation
    assert read(observation)["observation_status"] == "unrecorded"
    assert read(observation)["kb_ids"] is None
    with db.orm_session() as session:
        session.query(BotKnowledgeBindingModel).update({"enabled": False})
    assert service.record_build_selection(publication_id=101)
    assert read(observation)["kb_ids"] == []
    assert read(observation)["observation_status"] == "last_recorded"


@pytest.mark.parametrize(
    "status",
    [
        "draft",
        "built",
        "validate_pub",
        "validating",
        "online_pub",
        "success",
        "failed",
        "upgraded",
    ],
)
def test_only_building_records(observation, status):
    db, service, _ = observation
    with db.orm_session() as session:
        session.query(BotPublishModel).filter_by(id=101).update({"status": status})
    assert not service.record_build_selection(publication_id=101)
    assert read(observation)["observation_status"] == "unrecorded"


def test_rebuild_write_failure_preserves_last_capture_and_native_state(observation):
    db, service, _ = observation
    assert service.record_build_selection(publication_id=101)
    original = read(observation)
    with db.orm_session() as session:
        session.query(BotKnowledgeBindingModel).update({"enabled": False})
    service.configs.put = Mock(side_effect=RuntimeError("write unavailable"))
    assert not service.record_build_selection(publication_id=101)
    assert read(observation) == original
    with db.orm_session() as session:
        assert (
            session.query(BotPublishModel).filter_by(id=101).one().status == "building"
        )


def test_rebuild_success_replaces_only_this_versions_observation(observation):
    db, service, _ = observation
    assert service.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        session.query(BotKnowledgeBindingModel).update({"enabled": False})
    assert service.record_build_selection(publication_id=101)
    assert read(observation)["kb_ids"] == []


def test_auxiliary_read_failure_is_unavailable_without_draft_fallback(observation):
    _, service, _ = observation
    service.configs.get = Mock(side_effect=RuntimeError("read unavailable"))
    result = read(observation)
    assert result["observation_status"] == "unavailable"
    assert result["kb_ids"] is None


@pytest.mark.parametrize(
    "value", ["bad-json", "{}", "[]", "null", '"text"', "x" * 65536]
)
def test_malformed_payload_is_unrecorded(observation, value):
    _, service, _ = observation
    service.configs.get = Mock(return_value=value)
    assert read(observation)["observation_status"] == "unrecorded"


@pytest.mark.parametrize(
    "field,value",
    [
        ("avernet_tenant", "other"),
        ("env", "prod"),
        ("source_bot_pk", 2),
        ("space_id", 999),
        ("publication_id", 102),
        ("version", 2),
        ("kb_ids", ["kb"] * 101),
        ("kb_ids", ["kb", "kb"]),
        ("kb_ids", [False]),
        ("captured_at", "yesterday"),
    ],
)
def test_wrong_or_invalid_record_is_not_a_selection(observation, field, value):
    db, service, _ = observation
    assert service.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        row = session.query(BotCommonConfig).one()
        payload = json.loads(row.config_value)
        payload[field] = value
        row.config_value = json.dumps(payload)
    assert read(observation)["observation_status"] == "unrecorded"


def test_authorization_errors_are_not_swallowed(observation):
    _, service, scope = observation
    service.configs.get = Mock(side_effect=AssertionError("must not query config"))
    with pytest.raises(KnowledgeError, match="BOT_EDITOR_REQUIRED"):
        service.get_publication_selection(
            scope=scope, actor_id="other", publication_id=101
        )
    service.configs.get.assert_not_called()


def test_old_owner_is_not_authority_but_current_owner_can_read(observation):
    _, service, scope = observation
    assert service.record_build_selection(publication_id=101)
    assert read(observation)["kb_ids"] == ["kb"]
    with pytest.raises(KnowledgeError):
        service.get_publication_selection(
            scope=scope, actor_id="legacy-owner", publication_id=101
        )


@pytest.mark.parametrize(
    "field,value", [("source_bot_pk", 2), ("source_bot_id", "bot2"), ("env", "prod")]
)
def test_foreign_publication_is_not_read_or_captured(observation, field, value):
    db, service, _ = observation
    with db.orm_session() as session:
        session.query(BotPublishModel).filter_by(id=101).update({field: value})
    with pytest.raises(KnowledgeError, match="PUBLICATION_NOT_FOUND"):
        read(observation)
    assert not service.record_build_selection(publication_id=101)


def test_tenant_isolation_includes_recording(observation):
    _, service, _ = observation
    assert service.record_build_selection(publication_id=101)
    with avernet_tenant_scope("different"):
        assert not service.record_build_selection(publication_id=101)
        with pytest.raises(KnowledgeError, match="BOT_NOT_FOUND"):
            read(observation)


def test_moved_space_does_not_inherit_old_selection(observation):
    db, service, _ = observation
    assert service.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        session.add(
            SpaceModel(
                id=2,
                space_code="new",
                space_type="PERSONAL",
                name="New",
                env="dev",
                created_by="owner",
                updated_by="owner",
            )
        )
        session.query(BotModel).filter_by(id=1).update({"space_id": 2})
    assert read(observation)["observation_status"] == "unrecorded"
    assert service.record_build_selection(publication_id=101)
    assert read(observation)["kb_ids"] == []


def test_invalid_space_does_not_overwrite_with_empty(observation):
    db, service, _ = observation
    assert service.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        session.query(SpaceMemberModel).filter_by(user_id="owner").update(
            {"status": "INACTIVE"}
        )
    assert not service.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        assert json.loads(session.query(BotCommonConfig).one().config_value)[
            "kb_ids"
        ] == ["kb"]


@pytest.mark.parametrize("publication_id", [True, 0, -1, "101"])
def test_invalid_publication_id_is_not_recorded(observation, publication_id):
    _, service, _ = observation
    assert not service.record_build_selection(publication_id=publication_id)
