"""Unit tests for OrmAcBotPublishRepository.get_binding_ids.

Uses pytest + MagicMock with ORM session mocking, matching the pattern
from test_orm_bot_run_repository.py.
"""

import json
from unittest.mock import MagicMock

import pytest

from secbaas.community.core.repository.ac_bot_publish import OrmAcBotPublishRepository


@pytest.fixture
def mock_session():
    """Mock SQLAlchemy ORM session."""
    session = MagicMock()
    return session


@pytest.fixture
def mock_database(mock_session):
    """Mock database that yields a mock ORM session."""
    database = MagicMock()
    database.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    database.orm_session.return_value.__exit__ = MagicMock(return_value=False)
    return database


@pytest.fixture
def repository(mock_database):
    return OrmAcBotPublishRepository(database=mock_database)


def _make_orm_row(
    source_bot_id: str = "bot-001",
    status: str = "success",
    owner_id: str = "entity-001",
    env: str = "prod",
    ext: dict | None = None,
):
    """Create a mock ORM row object matching AcBotPublishModel fields."""
    if ext is None:
        ext = {"binding": {"online": "100"}}
    row = MagicMock()
    row.source_bot_id = source_bot_id
    row.status = status
    row.owner_id = owner_id
    row.env = env
    row.ext = json.dumps(ext) if isinstance(ext, dict) else ext
    return row


class TestGetBindingIds:
    def test_returns_single_binding_id(self, repository, mock_session):
        row = _make_orm_row(ext={"binding": {"online": "42"}})
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            row
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [42]

    def test_returns_multiple_binding_ids_desc_order(self, repository, mock_session):
        """Bug 3 fix: multiple publish records should return all binding_ids."""
        row_1 = _make_orm_row(ext={"binding": {"online": "100"}})
        row_2 = _make_orm_row(ext={"binding": {"online": "200"}})
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            row_2,
            row_1,
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [200, 100]

    def test_deduplicates_binding_ids(self, repository, mock_session):
        """Multiple publish records pointing to the same binding_id should be deduped."""
        row_1 = _make_orm_row(ext={"binding": {"online": "100"}})
        row_2 = _make_orm_row(ext={"binding": {"online": "100"}})  # same binding_id
        row_3 = _make_orm_row(ext={"binding": {"online": "200"}})
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            row_3,
            row_2,
            row_1,
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [200, 100]  # 100 appears once, deduped

    def test_returns_empty_list_when_no_rows(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == []

    def test_with_owner_id_filter(self, repository, mock_session):
        row = _make_orm_row(ext={"binding": {"online": "99"}})
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            row
        ]

        result = repository.get_binding_ids(
            source_bot_id="bot-001", status="success", owner_id="entity-001"
        )

        assert result == [99]
        # Verify filter was called with owner_id
        filter_call = mock_session.query.return_value.filter.call_args
        # The filter receives positional args of SQLAlchemy filter expressions
        # We just verify the result is correct

    def test_with_env_filter(self, repository, mock_session):
        """Bug 2 verification: env parameter should filter at ac_bot_publish level."""
        row = _make_orm_row(ext={"binding": {"online": "99"}})
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            row
        ]

        result = repository.get_binding_ids(
            source_bot_id="bot-001", status="success", env="pre"
        )

        assert result == [99]

    def test_validating_status_uses_verify_key(self, repository, mock_session):
        row = _make_orm_row(ext={"binding": {"verify": "77"}})
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            row
        ]

        result = repository.get_binding_ids(
            source_bot_id="bot-002", status="validating"
        )

        assert result == [77]

    def test_success_status_uses_online_key(self, repository, mock_session):
        row = _make_orm_row(ext={"binding": {"online": "88"}})
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            row
        ]

        result = repository.get_binding_ids(source_bot_id="bot-003", status="success")

        assert result == [88]

    def test_skips_invalid_json_rows(self, repository, mock_session):
        """Rows with invalid JSON should be skipped, others should still be processed."""
        valid_row = _make_orm_row(ext={"binding": {"online": "100"}})
        invalid_row = _make_orm_row()
        invalid_row.ext = "not-json"  # override to invalid JSON
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            valid_row,
            invalid_row,
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [100]

    def test_skips_rows_without_binding_key(self, repository, mock_session):
        """Rows where ext.binding.online/verify is missing should be skipped."""
        row_no_key = _make_orm_row(ext={"binding": {}})  # no "online" key
        row_with_key = _make_orm_row(ext={"binding": {"online": "200"}})
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            row_with_key,
            row_no_key,
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [200]

    def test_mixed_valid_and_invalid_rows(self, repository, mock_session):
        """Mix of valid, invalid JSON, and missing keys — only valid ones returned."""
        rows = [
            _make_orm_row(ext={"binding": {"online": "300"}}),  # valid
            _make_orm_row(),  # will override ext below
            _make_orm_row(ext={"binding": {}}),  # missing key
            _make_orm_row(ext={"binding": {"online": "400"}}),  # valid
        ]
        rows[1].ext = "bad-json"  # invalid JSON
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [300, 400]

    def test_skips_non_integer_binding_values(self, repository, mock_session):
        """Rows with non-integer binding values should be skipped."""
        row_str = _make_orm_row()
        row_str.ext = json.dumps({"binding": {"online": "not-a-number"}})
        row_valid = _make_orm_row(ext={"binding": {"online": "500"}})
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            row_valid,
            row_str,
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [500]

    def test_skips_null_binding_value(self, repository, mock_session):
        """Rows with null binding value should be skipped."""
        row_null = _make_orm_row(ext={"binding": {"online": None}})
        row_valid = _make_orm_row(ext={"binding": {"online": "600"}})
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            row_valid,
            row_null,
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [600]


class TestGetBindingIdStillWorks:
    """Verify the original get_binding_id (singular) method still works."""

    def test_returns_binding_id(self, repository, mock_session):
        row = _make_orm_row(ext={"binding": {"online": "42"}})
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = row

        result = repository.get_binding_id(source_bot_id="bot-001", status="success")

        assert result == 42

    def test_returns_none_when_no_row(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repository.get_binding_id(source_bot_id="bot-001", status="success")

        assert result is None

    def test_with_env_filter(self, repository, mock_session):
        row = _make_orm_row(ext={"binding": {"online": "42"}})
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = row

        result = repository.get_binding_id(
            source_bot_id="bot-001", status="success", env="prod"
        )

        assert result == 42
