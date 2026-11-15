"""Expected legacy skips must not hide unexpected observation failures."""

import json
import logging
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel,
)
from agentclaw.community.core.bot_knowledge.stage_selection import (
    BotKnowledgeStageSelection,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.plugin_api.models import BotCommonConfig, BotModel


def run_selection(db, observer, phase):
    if phase == "build":
        return observer.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        publication = session.get(BotPublishModel, 101)
        publication.status = "success"
        publication.ext = json.dumps({"publish": {"online": 301}})
    return observer.project_online_selection(
        publication_id=101,
        observed={
            "workflow_id": 301,
            "rollback_token": None,
        },
    )


def diagnostic(caplog, message):
    records = [r for r in caplog.records if r.getMessage().startswith(message)]
    assert len(records) == 1
    return records[0]


@pytest.mark.parametrize("phase", ["build", "online"])
def test_legacy_bot_without_personal_space_warns_without_writing(
    observation, caplog, phase
):
    db, observer, _ = observation
    with db.orm_session() as session:
        session.get(BotModel, 1).space_id = None
        binding = session.query(BotKnowledgeBindingModel).one()
        before = (binding.enabled, binding.online_enabled, binding.revision)
    with caplog.at_level(logging.WARNING, logger="start"):
        assert run_selection(db, observer, phase) is False
    record = diagnostic(
        caplog,
        "Knowledge observation skipped"
        if phase == "build"
        else "Knowledge online projection skipped",
    )
    assert record.levelno == logging.WARNING
    assert "publication_id=101" in record.getMessage()
    assert "reason=SPACE_NOT_FOUND" in record.getMessage()
    assert record.exc_info is None
    with db.orm_session() as session:
        assert session.get(BotModel, 1).space_id is None
        assert session.query(BotCommonConfig).count() == 0
        binding = session.query(BotKnowledgeBindingModel).one()
        assert (binding.enabled, binding.online_enabled, binding.revision) == before
        assert session.get(BotPublishModel, 101).status == (
            "building" if phase == "build" else "success"
        )


@pytest.mark.parametrize("phase", ["build", "online"])
@pytest.mark.parametrize(
    "space_state", ["missing", "deleted", "deleted_personal", "membership"]
)
def test_broken_space_is_still_an_error(observation, caplog, phase, space_state):
    db, observer, _ = observation
    with db.orm_session() as session:
        bot = session.get(BotModel, 1)
        if space_state == "missing":
            bot.space_id = 999
        elif space_state == "deleted":
            session.get(SpaceModel, 1).deleted_at = datetime.now(UTC)
        elif space_state == "membership":
            session.query(SpaceMemberModel).filter_by(user_id="owner").delete()
        else:
            bot.space_id = None
            session.add(
                SpaceModel(
                    id=2,
                    space_code="personal",
                    space_type="PERSONAL",
                    name="Personal",
                    env="dev",
                    personal_owner_id="owner",
                    created_by="owner",
                    updated_by="owner",
                    deleted_at=datetime.now(UTC),
                )
            )
    with caplog.at_level(logging.WARNING, logger="start"):
        assert run_selection(db, observer, phase) is False
    record = diagnostic(
        caplog,
        "Knowledge observation not recorded"
        if phase == "build"
        else "Knowledge online projection not saved",
    )
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], KnowledgeError)
    assert record.exc_info[1].code == (
        "SPACE_MEMBER_REQUIRED" if space_state == "membership" else "SPACE_NOT_FOUND"
    )


def test_null_space_with_personal_fallback_still_records(observation, caplog):
    db, observer, _ = observation
    with db.orm_session() as session:
        session.get(BotModel, 1).space_id = None
        # Reuse the fixture's binding space as the owner's personal space.
        space = session.get(SpaceModel, 1)
        space.space_type = "PERSONAL"
        space.personal_owner_id = "owner"
    with caplog.at_level(logging.WARNING, logger="start"):
        assert observer.record_build_selection(publication_id=101) is True
        assert run_selection(db, observer, "online") is True
    assert not caplog.records
    with db.orm_session() as session:
        assert session.get(BotModel, 1).space_id is None
        assert session.query(BotCommonConfig).count() == 2
        assert session.query(BotKnowledgeBindingModel).one().online_enabled is True


def test_unexpected_observation_write_failure_keeps_traceback(observation, caplog):
    db, observer, _ = observation
    failure = RuntimeError("observation storage unavailable")
    observer.configs.put = Mock(side_effect=failure)
    with caplog.at_level(logging.WARNING, logger="start"):
        assert observer.record_build_selection(publication_id=101) is False
    record = diagnostic(caplog, "Knowledge observation not recorded")
    assert record.levelno == logging.ERROR
    assert record.exc_info[1] is failure
    with db.orm_session() as session:
        assert session.get(BotPublishModel, 101).status == "building"
        assert session.query(BotCommonConfig).count() == 0


@pytest.mark.parametrize("operation", ["read", "identity", "projection", "publication"])
def test_unexpected_read_and_transaction_failures_keep_tracebacks(
    observation, monkeypatch, caplog, operation
):
    db, observer, scope = observation
    reader = BotKnowledgeStageSelection(db)
    with db.orm_session() as session:
        bot = session.get(BotModel, 1).to_dict()
    failure = RuntimeError("selection database unavailable")
    if operation == "publication":
        monkeypatch.setattr(observer, "_read_record", Mock(side_effect=failure))
    else:
        monkeypatch.setattr(
            db,
            "transactional_orm_session" if operation == "projection" else "orm_session",
            Mock(side_effect=failure),
        )
    with caplog.at_level(logging.WARNING, logger="start"):
        if operation == "read":
            assert (
                reader.read(bot=bot, runtime_stage="online")["selection_status"]
                == "unavailable"
            )
            message = "Knowledge stage selection unavailable"
        elif operation == "identity":
            assert reader.observe_online(publication_id=101) is None
            message = "Knowledge deployment identity unavailable"
        elif operation == "projection":
            assert reader.project_online(publication_id=101, observed={}) is False
            message = "Knowledge online projection not saved"
        else:
            assert (
                observer.get_publication_selection(
                    scope=scope, actor_id="owner", publication_id=101
                )["observation_status"]
                == "unavailable"
            )
            message = "Knowledge observation unavailable"
    record = diagnostic(caplog, message)
    assert record.levelno == logging.ERROR
    assert record.exc_info[1] is failure
