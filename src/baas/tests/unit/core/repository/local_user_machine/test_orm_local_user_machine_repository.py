"""OrmLocalUserMachineRepository unit tests.

Uses pytest + MagicMock SQLAlchemy ORM session pattern matching the existing
test_orm_bot_session_repository.py and test_orm_device_repository.py.

Covers all methods from OrmLocalUserMachineRepository:
insert_machine, get_by_machine_id, list_by_user_id,
update_heartbeat, update_status, update_instance, update_machine_info,
update_route_info, clear_route_info, get_route_info.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from secbaas.community.core.repository.local_user_machine import (
    LocalUserMachineRecord,
    OrmLocalUserMachineRepository,
)

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


@pytest.fixture
def repository(mock_database):
    """Create OrmLocalUserMachineRepository with mocked database."""
    return OrmLocalUserMachineRepository(database=mock_database)


# ==================== Model Helper ====================

NOW = datetime(2026, 1, 15, 12, 0, 0)


def _make_mock_model(**kwargs):
    """Create a mock LocalUserMachineModel with to_record() support.

    Override specific fields via keyword arguments.
    """
    defaults: dict = {
        "id": 1,
        "gmt_create": NOW,
        "gmt_modified": NOW,
        "template_id": 100,
        "user_id": "user-001",
        "machine_id": "machine-001",
        "machine_info": json.dumps({"os": "macOS"}, ensure_ascii=False),
        "last_heartbeat": NOW,
        "connected_server_instance": "instance-1",
        "status": "ONLINE",
        "env": "dev",
        "connected_route_info": None,
    }
    defaults.update(kwargs)

    class FakeModel:
        pass

    model = FakeModel()
    for k, v in defaults.items():
        object.__setattr__(model, k, v)

    def _to_record():
        try:
            mi = (
                json.loads(model.machine_info)
                if isinstance(model.machine_info, str)
                else model.machine_info or {}
            )
        except (json.JSONDecodeError, TypeError):
            mi = {}
        try:
            cri = (
                json.loads(model.connected_route_info)
                if isinstance(model.connected_route_info, str)
                else model.connected_route_info
            )
        except (json.JSONDecodeError, TypeError):
            cri = None
        return LocalUserMachineRecord(
            id=model.id,
            gmt_create=model.gmt_create,
            gmt_modified=model.gmt_modified,
            template_id=model.template_id,
            user_id=model.user_id,
            machine_id=model.machine_id,
            machine_info=mi or {},
            last_heartbeat=model.last_heartbeat,
            connected_server_instance=model.connected_server_instance or "",
            status=model.status,
            env=model.env,
            connected_route_info=cri,
        )

    model.to_record = MagicMock(side_effect=_to_record)
    return model


# ==================== insert_machine ====================


class TestInsertMachine:
    def test_insert_returns_id(self, repository, mock_session):
        mock_session.add = MagicMock()
        mock_session.flush = MagicMock()

        model = _make_mock_model(id=42, machine_id="machine-001")

        def _capture_add(model_arg):
            model_arg.id = model.id

        mock_session.add.side_effect = _capture_add

        result = repository.insert_machine(
            template_id=100,
            user_id="user-001",
            machine_id="machine-001",
            machine_info={"os": "macOS"},
            last_heartbeat=NOW,
            connected_server_instance="instance-1",
            status="ONLINE",
            env="dev",
        )

        assert result == 42
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        added_model = mock_session.add.call_args[0][0]
        assert added_model.template_id == 100
        assert added_model.user_id == "user-001"
        assert added_model.machine_id == "machine-001"
        assert added_model.env == "dev"
        assert added_model.status == "ONLINE"
        assert added_model.connected_server_instance == "instance-1"

    def test_insert_with_none_machine_info(self, repository, mock_session):
        mock_session.add = MagicMock()
        mock_session.flush = MagicMock()
        mock_session.add.side_effect = lambda m: setattr(m, "id", 1)

        repository.insert_machine(
            template_id=200,
            user_id="user-002",
            machine_id="machine-002",
            machine_info=None,
            last_heartbeat=NOW,
            connected_server_instance="instance-2",
            status="OFFLINE",
            env="prod",
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.machine_info is None
        assert added_model.template_id == 200
        assert added_model.user_id == "user-002"


# ==================== get_by_machine_id ====================


class TestGetByMachineId:
    def test_found(self, repository, mock_session):
        model = _make_mock_model(id=5, machine_id="machine-xyz", user_id="user-abc")
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repository.get_by_machine_id("machine-xyz", "dev")

        assert result is not None
        assert isinstance(result, LocalUserMachineRecord)
        assert result.id == 5
        assert result.machine_id == "machine-xyz"
        assert result.user_id == "user-abc"
        model.to_record.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_machine_id("nonexistent", "dev")

        assert result is None


# ==================== list_by_user_id ====================


class TestListByUserId:
    def test_returns_list(self, repository, mock_session):
        model1 = _make_mock_model(id=1, user_id="user-001")
        model2 = _make_mock_model(id=2, user_id="user-001")
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            model1,
            model2,
        ]

        result = repository.list_by_user_id("user-001", "dev")

        assert len(result) == 2
        assert all(r.user_id == "user-001" for r in result)
        model1.to_record.assert_called_once()
        model2.to_record.assert_called_once()

    def test_empty_list(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repository.list_by_user_id("user-empty", "dev")

        assert result == []


# ==================== update_heartbeat ====================


class TestUpdateHeartbeat:
    def test_updates_heartbeat(self, repository, mock_session):
        ts = datetime(2025, 1, 15, 12, 0, 0)

        repository.update_heartbeat("machine-001", "dev", ts)

        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["last_heartbeat"] == ts
        assert "gmt_modified" in update_dict


# ==================== update_status ====================


class TestUpdateStatus:
    def test_updates_to_offline(self, repository, mock_session):
        repository.update_status("machine-001", "dev", "OFFLINE")

        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "OFFLINE"
        assert "gmt_modified" in update_dict


# ==================== update_instance ====================


class TestUpdateInstance:
    def test_updates_instance(self, repository, mock_session):
        repository.update_instance("machine-001", "dev", "instance-5")

        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["connected_server_instance"] == "instance-5"
        assert "gmt_modified" in update_dict


# ==================== update_machine_info ====================


class TestUpdateMachineInfo:
    def test_updates_with_dict(self, repository, mock_session):
        repository.update_machine_info("machine-001", "dev", {"os": "linux", "cpu": 4})

        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert "os" in update_dict["machine_info"]
        assert "cpu" in update_dict["machine_info"]
        assert "gmt_modified" in update_dict

    def test_updates_with_none(self, repository, mock_session):
        repository.update_machine_info("machine-001", "dev", None)

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["machine_info"] is None


# ==================== update_route_info ====================


class TestUpdateRouteInfo:
    def test_updates_route_info(self, repository, mock_session):
        repository.update_route_info(
            "machine-001", "dev", {"host": "10.0.0.1", "port": 8080}
        )

        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert "host" in update_dict["connected_route_info"]
        assert '"port": 8080' in update_dict["connected_route_info"]
        assert "gmt_modified" in update_dict


# ==================== clear_route_info ====================


class TestClearRouteInfo:
    def test_clears_route_info(self, repository, mock_session):
        repository.clear_route_info("machine-001", "dev")

        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["connected_route_info"] is None
        assert "gmt_modified" in update_dict


# ==================== get_route_info ====================


class TestGetRouteInfo:
    def test_found(self, repository, mock_session):
        route_data = json.dumps({"host": "10.0.0.1", "port": 8080})
        mock_session.query.return_value.filter.return_value.first.return_value = (
            route_data,
        )

        result = repository.get_route_info("machine-001", "dev")

        assert result == {"host": "10.0.0.1", "port": 8080}

    def test_found_json_string(self, repository, mock_session):
        route_data = json.dumps({"host": "10.0.0.2"})
        mock_session.query.return_value.filter.return_value.first.return_value = (
            route_data,
        )

        result = repository.get_route_info("machine-001", "dev")

        assert result == {"host": "10.0.0.2"}

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_route_info("nonexistent", "dev")

        assert result is None

    def test_not_found_empty_row(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = (None,)

        result = repository.get_route_info("machine-001", "dev")

        assert result is None

    def test_parse_error_returns_none(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = (
            "not-valid-json",
        )

        result = repository.get_route_info("machine-001", "dev")

        assert result is None


# ==================== to_record (model method) ====================


class TestToRecord:
    def test_converts_valid_model(self):
        model = _make_mock_model(
            id=7,
            template_id=200,
            user_id="user-xyz",
            machine_id="mac-007",
            machine_info=json.dumps({"hostname": "devbox"}, ensure_ascii=False),
            status="ONLINE",
            env="prod",
        )
        # to_record is a MagicMock with side_effect, so call it and get real result
        record = model.to_record()

        assert record.id == 7
        assert record.machine_id == "mac-007"
        assert record.user_id == "user-xyz"
        assert record.status == "ONLINE"
        assert record.machine_info == {"hostname": "devbox"}

    def test_invalid_json_returns_empty_dict(self):
        model = _make_mock_model(
            id=1,
            machine_info="not-valid-json",
        )
        record = model.to_record()

        assert record.machine_info == {}

    def test_connected_route_info_parse_error(self):
        model = _make_mock_model(
            id=1,
            connected_route_info="bad-json",
        )
        record = model.to_record()

        assert record.connected_route_info is None
