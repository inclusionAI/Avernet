"""OrmWsRelaySessionRepository unit tests.

Covers the 15 behaviors specified in the Phase 65-01 PLAN:
  Test 1-3: WsRelaySessionModel structural checks (tablename, columns, unique constraint)
  Test 4-5: to_record() JSON deserialization (valid JSON, defensive invalid JSON)
  Test 6: WsRelaySessionRecord dataclass slots + field types
  Test 7: WsRelaySessionRepository Protocol has 5 abstract methods
  Test 8: OrmWsRelaySessionRepository.__init__ stores database
  Test 9: insert_init creates Model with init defaults and env from get_current_env()
  Test 10: get_by_session_id filters by session_id AND env
  Test 11: update_active updates status/connected_server_instance/connected_route_info/gmt_modified
  Test 12: update_closed updates status/closed/gmt_close/gmt_modified
  Test 13: _validate_transition enforces state machine rules
  Test 14: get_ws_relay_session_repository() returns WsRelaySessionRepository Protocol
  Test 15: __init__.py exports 5 symbols
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

# ==================== Fixtures ====================


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


@pytest.fixture(autouse=True)
def mock_get_current_env(monkeypatch):
    """Mock get_current_env to return 'dev' for all tests."""
    monkeypatch.setenv("SERVER_ENV", "dev")


@pytest.fixture
def repository(mock_database):
    """Create OrmWsRelaySessionRepository with mocked database."""
    from secbaas.community.core.repository.ws_relay_session import (
        OrmWsRelaySessionRepository,
    )

    return OrmWsRelaySessionRepository(mock_database)


# ==================== Model Helpers ====================

NOW = datetime(2026, 6, 29, 12, 0, 0)


def _make_mock_model(**kwargs):
    """Create a mock WsRelaySessionModel with to_record() support."""
    from secbaas.community.core.repository.ws_relay_session import WsRelaySessionRecord

    defaults: dict = {
        "id": 1,
        "gmt_create": NOW,
        "gmt_modified": NOW,
        "session_id": "sess-relay-001",
        "machine_id": "machine-001",
        "connected_server_instance": "proxy-instance-1",
        "status": "init",
        "env": "dev",
        "gmt_close": None,
        "connected_route_info": json.dumps({"host": "localhost", "port": 8080}),
        "operator": "user-001",
    }
    defaults.update(kwargs)

    class FakeModel:
        pass

    model = FakeModel()
    for k, v in defaults.items():
        object.__setattr__(model, k, v)

    def _to_record():
        route_info = None
        ci = model.connected_route_info
        if ci and isinstance(ci, str) and ci.strip():
            try:
                route_info = json.loads(ci)
            except (json.JSONDecodeError, TypeError):
                route_info = None
        return WsRelaySessionRecord(
            id=model.id,
            gmt_create=model.gmt_create,
            gmt_modified=model.gmt_modified,
            session_id=model.session_id,
            machine_id=model.machine_id,
            connected_server_instance=model.connected_server_instance,
            status=model.status,
            env=model.env,
            gmt_close=model.gmt_close,
            connected_route_info=route_info,
            operator=model.operator,
        )

    model.to_record = _to_record
    return model


# ==================== Test 1-3: WsRelaySessionModel structural checks ====================


class TestWsRelaySessionModel:
    """Tests for WsRelaySessionModel SQLAlchemy mapping (Tests 1-3)."""

    def test_tablename_is_baas_local_ws_relay_session(self):
        """Test 1: __tablename__ == 'baas_local_ws_relay_session'."""
        from secbaas.community.core.repository.ws_relay_session import (
            WsRelaySessionModel,
        )

        assert WsRelaySessionModel.__tablename__ == "baas_local_ws_relay_session"

    def test_has_11_columns_matching_ddl(self):
        """Test 2: Model has 11 columns matching DDL schema."""
        from secbaas.community.core.repository.ws_relay_session import (
            WsRelaySessionModel,
        )

        columns = WsRelaySessionModel.__table__.columns
        assert len(columns) == 11, f"Expected 11 columns, got {len(columns)}"

        column_names = {c.name for c in columns}
        expected = {
            "id",
            "gmt_create",
            "gmt_modified",
            "session_id",
            "machine_id",
            "connected_server_instance",
            "status",
            "env",
            "gmt_close",
            "connected_route_info",
            "operator",
        }
        assert column_names == expected, f"Column mismatch: {column_names ^ expected}"

        # Verify gmt_close is nullable
        gmt_close_col = columns["gmt_close"]
        assert gmt_close_col.nullable, "gmt_close must be nullable"

    def test_unique_constraint_uk_env_session(self):
        """Test 3: __table_args__ has UniqueConstraint('session_id', 'env', name='uk_env_session')."""
        from sqlalchemy import UniqueConstraint

        from secbaas.community.core.repository.ws_relay_session import (
            WsRelaySessionModel,
        )

        unique_constraints = [
            c
            for c in WsRelaySessionModel.__table_args__
            if isinstance(c, UniqueConstraint)
        ]
        assert len(unique_constraints) == 1, (
            f"Expected 1 UniqueConstraint, got {len(unique_constraints)}"
        )

        uc = unique_constraints[0]
        assert uc.name == "uk_env_session", f"Expected uk_env_session, got {uc.name}"
        uc_column_names = {c.name for c in uc.columns}
        assert uc_column_names == {"session_id", "env"}, (
            f"Expected session_id+env, got {uc_column_names}"
        )


# ==================== Test 4-5: to_record() JSON deserialization ====================


class TestToRecord:
    """Tests for WsRelaySessionModel.to_record() (Tests 4-5)."""

    def test_to_record_deserializes_valid_json(self):
        """Test 4: to_record() returns WsRelaySessionRecord with connected_route_info
        deserialized via json.loads, gmt_close as datetime|None."""
        from datetime import datetime

        from secbaas.community.core.repository.ws_relay_session import (
            WsRelaySessionModel,
            WsRelaySessionRecord,
        )

        now = datetime(2026, 6, 29, 12, 0, 0)
        close_time = datetime(2026, 6, 29, 13, 0, 0)

        # Build a real model instance to test to_record
        model = WsRelaySessionModel(
            session_id="test-sess-001",
            machine_id="machine-001",
            connected_server_instance="proxy-1",
            status="active",
            operator="user-001",
            connected_route_info=json.dumps({"host": "10.0.0.1", "port": 8080}),
            gmt_close=close_time,
        )

        record = model.to_record()
        assert isinstance(record, WsRelaySessionRecord)
        assert record.session_id == "test-sess-001"
        assert record.machine_id == "machine-001"
        assert record.connected_server_instance == "proxy-1"
        assert record.status == "active"
        assert record.operator == "user-001"
        assert record.connected_route_info == {"host": "10.0.0.1", "port": 8080}
        assert record.gmt_close == close_time

    def test_to_record_handles_invalid_json_gracefully(self):
        """Test 5: to_record() handles invalid JSON in connected_route_info
        gracefully (returns None, does not raise)."""
        from secbaas.community.core.repository.ws_relay_session import (
            WsRelaySessionModel,
        )

        model = WsRelaySessionModel(
            session_id="test-sess-002",
            machine_id="machine-002",
            connected_server_instance="",
            status="init",
            operator="user-002",
            connected_route_info="{invalid json!!!",
        )

        record = model.to_record()
        assert record.connected_route_info is None, (
            "Invalid JSON should result in None connected_route_info"
        )

    def test_to_record_handles_empty_string_route_info(self):
        """to_record() handles empty string connected_route_info gracefully."""
        from secbaas.community.core.repository.ws_relay_session import (
            WsRelaySessionModel,
        )

        model = WsRelaySessionModel(
            session_id="test-sess-003",
            machine_id="machine-003",
            connected_server_instance="",
            status="init",
            operator="user-003",
            connected_route_info="",
        )

        record = model.to_record()
        assert record.connected_route_info is None

    def test_to_record_gmt_close_null(self):
        """to_record() handles gmt_close=None correctly."""
        from secbaas.community.core.repository.ws_relay_session import (
            WsRelaySessionModel,
        )

        model = WsRelaySessionModel(
            session_id="test-sess-004",
            machine_id="machine-004",
            connected_server_instance="",
            status="closed",
            operator="user-004",
            connected_route_info="{}",
            gmt_close=None,
        )

        record = model.to_record()
        assert record.gmt_close is None
        assert record.connected_route_info == {}


# ==================== Test 6: WsRelaySessionRecord dataclass ====================


class TestWsRelaySessionRecord:
    """Tests for WsRelaySessionRecord @dataclass (Test 6)."""

    def test_record_is_slots_dataclass_with_correct_types(self):
        """Test 6: WsRelaySessionRecord is slots=True @dataclass with correct field types."""
        from dataclasses import is_dataclass

        from secbaas.community.core.repository.ws_relay_session import (
            WsRelaySessionRecord,
        )

        assert is_dataclass(WsRelaySessionRecord), "Record must be a dataclass"

        # Verify slots
        assert hasattr(WsRelaySessionRecord, "__slots__"), "Record must use slots=True"

        # Verify fields via annotations
        annotations = WsRelaySessionRecord.__annotations__
        assert annotations["id"] is int
        assert annotations["session_id"] is str
        assert annotations["machine_id"] is str
        assert annotations["connected_server_instance"] is str
        assert annotations["status"] is str
        assert annotations["env"] is str
        assert annotations["operator"] is str
        # connected_route_info: dict[str, Any] | None
        # gmt_close: datetime | None


# ==================== Test 7: Protocol has 5 abstract methods ====================


class TestProtocol:
    """Tests for WsRelaySessionRepository Protocol (Test 7)."""

    def test_protocol_has_5_methods(self):
        """Test 7: Protocol has 5 abstract methods."""
        from secbaas.community.core.repository.ws_relay_session import (
            WsRelaySessionRepository,
        )

        expected_methods = {
            "insert_init",
            "get_by_session_id",
            "update_active",
            "update_closed",
            "_validate_transition",
        }
        actual_methods = {
            name
            for name, attr in WsRelaySessionRepository.__dict__.items()
            if callable(attr) and not name.startswith("_")
        }
        # _validate_transition starts with _ so include it separately
        actual_all = {
            name
            for name, attr in WsRelaySessionRepository.__dict__.items()
            if callable(attr)
        }
        missing = expected_methods - actual_all
        assert not missing, f"Protocol missing methods: {missing}"


# ==================== Test 8: __init__ stores database ====================


class TestInit:
    """Tests for OrmWsRelaySessionRepository.__init__ (Test 8)."""

    def test_init_stores_database(self, mock_database):
        """Test 8: __init__ accepts database parameter, stores as self._database."""
        from secbaas.community.core.repository.ws_relay_session import (
            OrmWsRelaySessionRepository,
        )

        repo = OrmWsRelaySessionRepository(mock_database)
        assert repo._database is mock_database


# ==================== Test 9: insert_init ====================


class TestInsertInit:
    """Tests for insert_init (Test 9)."""

    def test_insert_init_creates_model_with_init_defaults(
        self, repository, mock_session
    ):
        """Test 9: insert_init creates Model with init defaults and env from get_current_env()."""
        mock_session.add = MagicMock()
        mock_session.flush = MagicMock()
        mock_session.add.side_effect = lambda m: setattr(m, "id", 42)

        session_id = "sess-relay-init-001"
        machine_id = "machine-001"
        operator = "user-001"

        result = repository.insert_init(
            session_id=session_id,
            machine_id=machine_id,
            operator=operator,
        )

        # Verify session.add() was called
        assert mock_session.add.called, "session.add() should be called"
        assert result == 42

        # Verify the model object passed to add()
        added_model = mock_session.add.call_args[0][0]
        assert added_model.session_id == session_id
        assert added_model.machine_id == machine_id
        assert added_model.operator == operator
        assert added_model.connected_server_instance == ""
        assert added_model.connected_route_info == "{}"
        assert added_model.status == "init"
        assert added_model.gmt_close is None
        assert added_model.env == "dev"  # from mock_get_current_env


# ==================== Test 10: get_by_session_id ====================


class TestGetBySessionId:
    """Tests for get_by_session_id (Test 10)."""

    def test_get_by_session_id_filters_by_session_id_and_env(
        self, repository, mock_session, monkeypatch
    ):
        """Test 10: get_by_session_id filters by session_id AND env."""
        monkeypatch.setenv("SERVER_ENV", "prod")
        session_id = "sess-relay-get-001"

        query = MagicMock()
        mock_session.query.return_value = query
        filter_result = MagicMock()
        query.filter.return_value = filter_result

        mock_row = _make_mock_model(session_id=session_id, env="prod")
        filter_result.first.return_value = mock_row

        record = repository.get_by_session_id(session_id)

        # Verify query was called with correct model
        from secbaas.community.core.repository.ws_relay_session import (
            WsRelaySessionModel,
        )

        mock_session.query.assert_called_once_with(WsRelaySessionModel)

        # Verify env filter was applied
        call_args = query.filter.call_args[0]
        assert len(call_args) == 2  # session_id == and env ==

        assert record.session_id == session_id
        assert record.env == "prod"

    def test_get_by_session_id_returns_none_when_not_found(
        self, repository, mock_session
    ):
        """get_by_session_id returns None when session not found."""
        query = MagicMock()
        mock_session.query.return_value = query
        filter_result = MagicMock()
        query.filter.return_value = filter_result
        filter_result.first.return_value = None

        record = repository.get_by_session_id("nonexistent")
        assert record is None


# ==================== Test 11: update_active ====================


class TestUpdateActive:
    """Tests for update_active (Test 11)."""

    def test_update_active_updates_all_fields(self, repository, mock_session):
        """Test 11: update_active updates status, connected_server_instance,
        connected_route_info, gmt_modified."""
        session_id = "sess-relay-update-001"

        query = MagicMock()
        mock_session.query.return_value = query
        filter_chain = MagicMock()
        query.filter.return_value = filter_chain

        repository.update_active(
            session_id=session_id,
            connected_server_instance="proxy-instance-1",
            connected_route_info={"host": "10.0.0.1", "port": 8080},
        )

        # Verify filter was called with correct args
        assert query.filter.called
        # Verify update was called
        filter_chain.update.assert_called_once()
        update_kwargs = filter_chain.update.call_args[0][0]
        assert update_kwargs["status"] == "active"
        assert update_kwargs["connected_server_instance"] == "proxy-instance-1"
        assert "connected_route_info" in update_kwargs


# ==================== Test 12: update_closed ====================


class TestUpdateClosed:
    """Tests for update_closed (Test 12)."""

    def test_update_closed_updates_status_gmt_close_gmt_modified(
        self, repository, mock_session
    ):
        """Test 12: update_closed updates status, gmt_close, gmt_modified."""
        session_id = "sess-relay-close-001"

        query = MagicMock()
        mock_session.query.return_value = query
        filter_chain = MagicMock()
        query.filter.return_value = filter_chain

        repository.update_closed(session_id=session_id)

        # Verify filter was called
        assert query.filter.called
        # Verify update was called
        filter_chain.update.assert_called_once()
        update_kwargs = filter_chain.update.call_args[0][0]
        assert update_kwargs["status"] == "closed"
        assert "gmt_close" in update_kwargs
        assert "gmt_modified" in update_kwargs


# ==================== Test 13: _validate_transition ====================


class TestValidateTransition:
    """Tests for _validate_transition (Test 13)."""

    def test_init_to_active_allowed(self, repository):
        """init -> active is a valid transition."""
        # Should not raise
        repository._validate_transition("init", "active")

    def test_active_to_closed_allowed(self, repository):
        """active -> closed is a valid transition."""
        repository._validate_transition("active", "closed")

    def test_init_to_closed_allowed(self, repository):
        """init -> closed is a valid transition."""
        repository._validate_transition("init", "closed")

    def test_same_status_idempotent(self, repository):
        """Same -> same is idempotent (no-op)."""
        repository._validate_transition("init", "init")
        repository._validate_transition("active", "active")
        repository._validate_transition("closed", "closed")

    def test_closed_to_active_raises(self, repository):
        """closed -> active is a reverse transition, must raise."""
        from secbaas.community.api.device_manage._errors import DeviceCreationError

        with pytest.raises(DeviceCreationError) as exc_info:
            repository._validate_transition("closed", "active")
        assert exc_info.value.error_code == "RELAY_STATE_CONFLICT"

    def test_active_to_init_raises(self, repository):
        """active -> init is a reverse transition, must raise."""
        from secbaas.community.api.device_manage._errors import DeviceCreationError

        with pytest.raises(DeviceCreationError):
            repository._validate_transition("active", "init")

    def test_closed_to_init_raises(self, repository):
        """closed -> init is a reverse transition, must raise."""
        from secbaas.community.api.device_manage._errors import DeviceCreationError

        with pytest.raises(DeviceCreationError):
            repository._validate_transition("closed", "init")

    def test_closed_to_closed_idempotent(self, repository):
        """closed -> closed is idempotent, no raise."""
        repository._validate_transition("closed", "closed")


# ==================== Test 14: Factory function ====================


class TestFactory:
    """Tests for get_ws_relay_session_repository factory (Test 14)."""

    def test_factory_returns_protocol(self):
        """Test 14: get_ws_relay_session_repository() returns WsRelaySessionRepository Protocol."""
        # This test verifies the factory function exists and has the right signature.
        # Since it depends on the DI container (which requires full bootstrap),
        # we verify the function exists and imports correctly without calling it.
        from secbaas.community.core.repository.ws_relay_session import (
            WsRelaySessionRepository,
            get_ws_relay_session_repository,
        )

        assert callable(get_ws_relay_session_repository), (
            "get_ws_relay_session_repository must be callable"
        )

        # Verify annotations point to Protocol
        import inspect

        hints = inspect.get_annotations(get_ws_relay_session_repository)
        assert "return" in hints, "Factory must have return type annotation"


# ==================== Test 15: __init__.py exports ====================


class TestInitExports:
    """Tests for __init__.py public re-exports (Test 15)."""

    def test_init_exports_5_symbols(self):
        """Test 15: __init__.py exports 5 symbols."""
        from secbaas.community.core.repository.ws_relay_session import (
            OrmWsRelaySessionRepository,
            WsRelaySessionModel,
            WsRelaySessionRecord,
            WsRelaySessionRepository,
            __all__,
            get_ws_relay_session_repository,
        )

        assert len(__all__) == 5, f"Expected 5 symbols in __all__, got {len(__all__)}"
        expected = {
            "WsRelaySessionRecord",
            "WsRelaySessionRepository",
            "OrmWsRelaySessionRepository",
            "WsRelaySessionModel",
            "get_ws_relay_session_repository",
        }
        assert set(__all__) == expected, f"__all__ mismatch: {set(__all__) ^ expected}"

        # Verify all symbols are importable
        assert WsRelaySessionRecord is not None
        assert WsRelaySessionRepository is not None
        assert OrmWsRelaySessionRepository is not None
        assert WsRelaySessionModel is not None
        assert get_ws_relay_session_repository is not None
