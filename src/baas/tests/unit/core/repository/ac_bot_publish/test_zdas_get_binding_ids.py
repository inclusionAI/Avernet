"""Unit tests for OrmAcBotPublishRepository.get_binding_ids.

Tests the new get_binding_ids method that returns all matching binding_ids
(deduplicated, ordered by id DESC), replacing the single-record get_binding_id
for the list_paas_device_by_bot use case.
"""

import json
from unittest.mock import MagicMock

import pytest

from secbaas.community.core.repository.ac_bot_publish import OrmAcBotPublishRepository


def _make_mock_row(ext_text: str):
    """Create a mock ORM row with an .ext attribute."""
    row = MagicMock()
    row.ext = ext_text
    return row


@pytest.fixture
def mock_orm_session():
    """Fixture that mocks a SQLAlchemy ORM session with query chain support."""
    session = MagicMock()

    # Mock: session.query().filter().order_by().all() → set via test
    _mock_query = MagicMock()
    _mock_filter = MagicMock()
    _mock_order = MagicMock()

    _mock_query.filter.return_value = _mock_filter
    _mock_filter.order_by.return_value = _mock_order

    session.query.return_value = _mock_query

    # Store for tests to set .all() return value
    session._mock_order = _mock_order
    return session


@pytest.fixture
def mock_database(mock_orm_session):
    """Fixture that mocks the database with orm_session() context manager."""
    database = MagicMock()
    database.orm_session.return_value.__enter__ = MagicMock(
        return_value=mock_orm_session
    )
    database.orm_session.return_value.__exit__ = MagicMock(return_value=False)
    return database


@pytest.fixture
def repository(mock_database):
    return OrmAcBotPublishRepository(mock_database)


class TestGetBindingIds:
    def test_returns_single_binding_id(
        self, repository, mock_database, mock_orm_session
    ):
        mock_orm_session._mock_order.all.return_value = [
            _make_mock_row(json.dumps({"binding": {"online": "42"}}))
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [42]

    def test_returns_multiple_binding_ids_desc_order(
        self, repository, mock_database, mock_orm_session
    ):
        """Multiple publish records → all binding_ids returned in query order."""
        mock_orm_session._mock_order.all.return_value = [
            _make_mock_row(json.dumps({"binding": {"online": "200"}})),
            _make_mock_row(json.dumps({"binding": {"online": "100"}})),
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [200, 100]

    def test_deduplicates_binding_ids(
        self, repository, mock_database, mock_orm_session
    ):
        """Multiple publish records pointing to same binding_id → deduped."""
        mock_orm_session._mock_order.all.return_value = [
            _make_mock_row(json.dumps({"binding": {"online": "200"}})),
            _make_mock_row(json.dumps({"binding": {"online": "100"}})),
            _make_mock_row(json.dumps({"binding": {"online": "100"}})),
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [200, 100]

    def test_returns_empty_list_when_no_rows(
        self, repository, mock_database, mock_orm_session
    ):
        mock_orm_session._mock_order.all.return_value = []

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == []

    def test_with_owner_id_filter(self, repository, mock_database, mock_orm_session):
        """owner_id parameter should filter result set."""
        mock_orm_session._mock_order.all.return_value = [
            _make_mock_row(json.dumps({"binding": {"online": "99"}}))
        ]

        result = repository.get_binding_ids(
            source_bot_id="bot-001", status="success", owner_id="entity-001"
        )

        # Verify the query was built with owner_id filter
        filter_call = mock_orm_session.query.return_value.filter.call_args[0]
        assert len(filter_call) == 3
        assert result == [99]

    def test_with_env_filter(self, repository, mock_database, mock_orm_session):
        """Bug 2 verification: env parameter should filter at ac_bot_publish level."""
        mock_orm_session._mock_order.all.return_value = [
            _make_mock_row(json.dumps({"binding": {"online": "99"}}))
        ]

        result = repository.get_binding_ids(
            source_bot_id="bot-001", status="success", env="pre"
        )

        filter_call = mock_orm_session.query.return_value.filter.call_args[0]
        assert len(filter_call) == 3
        assert result == [99]

    def test_without_env_filter(self, repository, mock_database, mock_orm_session):
        """When env is not passed, no env filter should be applied."""
        mock_orm_session._mock_order.all.return_value = [
            _make_mock_row(json.dumps({"binding": {"online": "99"}}))
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        filter_call = mock_orm_session.query.return_value.filter.call_args[0]
        assert len(filter_call) == 2
        assert result == [99]

    def test_validating_status_uses_verify_key(
        self, repository, mock_database, mock_orm_session
    ):
        mock_orm_session._mock_order.all.return_value = [
            _make_mock_row(json.dumps({"binding": {"verify": "77"}}))
        ]

        result = repository.get_binding_ids(
            source_bot_id="bot-002", status="validating"
        )

        assert result == [77]

    def test_success_status_uses_online_key(
        self, repository, mock_database, mock_orm_session
    ):
        mock_orm_session._mock_order.all.return_value = [
            _make_mock_row(json.dumps({"binding": {"online": "88"}}))
        ]

        result = repository.get_binding_ids(source_bot_id="bot-003", status="success")

        assert result == [88]

    def test_skips_invalid_json_rows(self, repository, mock_database, mock_orm_session):
        """Rows with invalid JSON should be skipped, others should still be processed."""
        mock_orm_session._mock_order.all.return_value = [
            _make_mock_row(json.dumps({"binding": {"online": "100"}})),
            _make_mock_row("not-json"),
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [100]

    def test_skips_rows_without_binding_key(
        self, repository, mock_database, mock_orm_session
    ):
        """Rows where ext.binding.online/verify is missing should be skipped."""
        mock_orm_session._mock_order.all.return_value = [
            _make_mock_row(json.dumps({"binding": {"online": "200"}})),
            _make_mock_row(json.dumps({"binding": {}})),  # no "online" key
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [200]

    def test_mixed_valid_and_invalid_rows(
        self, repository, mock_database, mock_orm_session
    ):
        """Mix of valid, invalid JSON, and missing keys — only valid ones returned."""
        mock_orm_session._mock_order.all.return_value = [
            _make_mock_row(json.dumps({"binding": {"online": "300"}})),
            _make_mock_row("bad-json"),
            _make_mock_row(json.dumps({"binding": {}})),
            _make_mock_row(json.dumps({"binding": {"online": "400"}})),
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [300, 400]

    def test_no_limit_clause(self, repository, mock_database, mock_orm_session):
        """get_binding_ids should use .all() not .first() (unlike get_binding_id)."""
        mock_orm_session._mock_order.all.return_value = [
            _make_mock_row(json.dumps({"binding": {"online": "1"}}))
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        mock_orm_session._mock_order.all.assert_called_once()
        assert result == [1]

    def test_skips_non_integer_binding_values(
        self, repository, mock_database, mock_orm_session
    ):
        """Rows with non-integer binding values should be skipped."""
        mock_orm_session._mock_order.all.return_value = [
            _make_mock_row(json.dumps({"binding": {"online": "500"}})),
            _make_mock_row(json.dumps({"binding": {"online": "not-a-number"}})),
        ]

        result = repository.get_binding_ids(source_bot_id="bot-001", status="success")

        assert result == [500]
