"""BotRunModel unit tests.

Tests for to_record(), _parse_metadata(), and _parse_result_extra().

Uses an in-memory SQLite session to create proper ORM instances,
since SQLAlchemy instrumented attributes cannot be set without a session.
"""

import json
from collections.abc import Generator
from datetime import datetime

import pytest
from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import Session, sessionmaker

from secbaas.community.core.repository.bot_run._orm_model import BotRunModel
from secbaas.community.core.repository.bot_run._record import BotRunRecord, RunStatus
from secbaas.community.spi.database import Base

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """In-memory SQLite engine with the baas_bot_run table created.

    SQLite does not auto-increment BIGINT PKs, so we convert them to INTEGER
    (same trick used by SqliteOrmPlugin.create_all_and_seed).
    """
    # Convert BIGINT PK columns to INTEGER so autoincrement works in SQLite
    for col in BotRunModel.__table__.primary_key.columns.values():
        if str(col.type).upper() == "BIGINT":
            col.type = Integer()

    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng, tables=[BotRunModel.__table__])
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    """SQLAlchemy session bound to the in-memory engine."""
    SessionLocal = sessionmaker(bind=engine)
    sess = SessionLocal()
    yield sess
    sess.rollback()
    sess.close()


def _make_model(**overrides):
    """Build a BotRunModel with sensible defaults (requires a session to flush)."""
    defaults = dict(
        run_id="run-001",
        bot_id="bot-001",
        api_key_prefix="ak_",
        message="hello",
        message_long="hello long",
        metadata_=None,
        status="PENDING",
        result_content=None,
        result_content_long=None,
        result_extra=None,
        error=None,
        completed_at=None,
    )
    defaults.update(overrides)
    return BotRunModel(**defaults)


# ---------------------------------------------------------------------------
# TestToRecord
# ---------------------------------------------------------------------------


class TestToRecord:
    def test_basic_conversion(self, session):
        model = _make_model(
            run_id="run-abc",
            bot_id="bot-xyz",
            api_key_prefix="ak_test",
            status="RUNNING",
        )
        session.add(model)
        session.flush()

        record = model.to_record()

        assert isinstance(record, BotRunRecord)
        assert record.run_id == "run-abc"
        assert record.bot_id == "bot-xyz"
        assert record.api_key_prefix == "ak_test"
        assert record.status == "RUNNING"

    def test_message_falls_back_to_short_when_no_long(self, session):
        model = _make_model(message="short msg", message_long=None)
        session.add(model)
        session.flush()

        record = model.to_record()

        # When message_long is None, message field is used
        assert record.message == "short msg"
        assert record.message_long is None

    def test_message_prefers_long(self, session):
        model = _make_model(message="short", message_long="long version")
        session.add(model)
        session.flush()

        record = model.to_record()

        assert record.message == "long version"
        assert record.message_long == "long version"

    def test_message_empty_when_both_none(self, session):
        model = _make_model(message=None, message_long=None)
        session.add(model)
        session.flush()

        record = model.to_record()

        assert record.message == ""
        assert record.message_long is None

    def test_result_content_falls_back_to_short(self, session):
        model = _make_model(result_content="short result", result_content_long=None)
        session.add(model)
        session.flush()

        record = model.to_record()

        assert record.result_content == "short result"
        assert record.result_content_long is None

    def test_result_content_prefers_long(self, session):
        model = _make_model(result_content="short", result_content_long="long result")
        session.add(model)
        session.flush()

        record = model.to_record()

        assert record.result_content == "long result"
        assert record.result_content_long == "long result"

    def test_result_content_empty_when_both_none(self, session):
        model = _make_model(result_content=None, result_content_long=None)
        session.add(model)
        session.flush()

        record = model.to_record()

        assert record.result_content == ""
        assert record.result_content_long is None

    def test_completed_fields(self, session):
        now = datetime.now()
        model = _make_model(
            status="COMPLETED",
            result_content_long="done",
            result_extra='{"tokens": 100}',
            completed_at=now,
        )
        session.add(model)
        session.flush()

        record = model.to_record()

        assert record.status == "COMPLETED"
        assert record.completed_at == now
        assert record.result_extra == {"tokens": 100}

    def test_failed_fields(self, session):
        model = _make_model(status="FAILED", error="timeout exceeded")
        session.add(model)
        session.flush()

        record = model.to_record()

        assert record.status == "FAILED"
        assert record.error == "timeout exceeded"

    def test_metadata_json_deserialized(self, session):
        model = _make_model(metadata_='{"source": "api", "version": 2}')
        session.add(model)
        session.flush()

        record = model.to_record()

        assert record.metadata == {"source": "api", "version": 2}


# ---------------------------------------------------------------------------
# TestParseMetadata
# ---------------------------------------------------------------------------


class TestParseMetadata:
    def test_valid_json_string(self, session):
        model = _make_model(metadata_='{"key": "value"}')
        session.add(model)
        session.flush()

        assert model._parse_metadata() == {"key": "value"}

    def test_none_returns_none(self, session):
        model = _make_model(metadata_=None)
        session.add(model)
        session.flush()

        assert model._parse_metadata() is None

    def test_invalid_json_returns_none(self, session):
        model = _make_model(metadata_="not valid json{{{")
        session.add(model)
        session.flush()

        assert model._parse_metadata() is None

    def test_empty_json_object(self, session):
        model = _make_model(metadata_="{}")
        session.add(model)
        session.flush()

        assert model._parse_metadata() == {}

    def test_json_array(self, session):
        model = _make_model(metadata_="[1, 2, 3]")
        session.add(model)
        session.flush()

        assert model._parse_metadata() == [1, 2, 3]


# ---------------------------------------------------------------------------
# TestParseResultExtra
# ---------------------------------------------------------------------------


class TestParseResultExtra:
    def test_valid_json_string(self, session):
        model = _make_model(result_extra='{"tokens": 150}')
        session.add(model)
        session.flush()

        assert model._parse_result_extra() == {"tokens": 150}

    def test_none_returns_none(self, session):
        model = _make_model(result_extra=None)
        session.add(model)
        session.flush()

        assert model._parse_result_extra() is None

    def test_invalid_json_returns_none(self, session):
        model = _make_model(result_extra="broken json}}}")
        session.add(model)
        session.flush()

        assert model._parse_result_extra() is None

    def test_empty_json_object(self, session):
        model = _make_model(result_extra="{}")
        session.add(model)
        session.flush()

        assert model._parse_result_extra() == {}

    def test_json_array(self, session):
        model = _make_model(result_extra='[{"a": 1}, {"b": 2}]')
        session.add(model)
        session.flush()

        assert model._parse_result_extra() == [{"a": 1}, {"b": 2}]
